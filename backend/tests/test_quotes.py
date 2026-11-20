from dataclasses import replace
from datetime import timedelta
from io import BytesIO

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pypdf import PdfReader

from forwarder.main import create_app
from forwarder.quotes import (
    QUOTE_TOOLS, QuoteInput, QuoteService, _today, build_quote_router, calculate_quote,
    quote_mock_request, register_quote_tools,
)
from forwarder.tools.registry import ToolContext, ToolRegistry
from forwarder.tickets.executor import InlineExecutor
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView


@pytest.fixture
def anyio_backend():
    return "asyncio"


def request(**changes):
    return {
        "origin": "CNTAO", "destination": "KRINC", "mode": "fcl", "goods": "家居用品",
        "ship_date": (_today() + timedelta(days=1)).isoformat(), "incoterm": "FOB",
        "container_type": "40HQ", "container_count": 2, **changes,
    }


def test_deterministic_quote_modes_and_policy():
    fcl = calculate_quote(QuoteInput.model_validate(request()))
    assert fcl["amount_usd"] == 2105 and not fcl["approval_required"]
    assert fcl["internal"]["cost_usd"] == 1596 and len(fcl["schedule"]) == 3
    lcl = calculate_quote(QuoteInput.model_validate(request(mode="lcl", volume_cbm="0.6")))
    assert lcl["amount_usd"] == 93
    ferry = calculate_quote(QuoteInput.model_validate(request(mode="ferry", volume_cbm="3", incoterm="DAP")))
    assert ferry["amount_usd"] == 541
    air = calculate_quote(QuoteInput.model_validate(request(
        mode="air", origin="CNTAO-AIR", destination="KRICN-AIR", incoterm="FCA", weight_kg=100, volume_cbm="2.1",
    )))
    freight = next(charge for charge in air["charges"] if charge["name_en"] == "International freight")
    assert freight["quantity"] == 351 and freight["amount_usd"] == 1088.10
    discounted = calculate_quote(QuoteInput.model_validate(request(discount_pct=15)))
    assert discounted["amount_usd"] == 1789.25 and discounted["approval_required"]
    assert discounted["internal"]["reasons"] == ["毛利低于底线", "折扣超过员工权限"]


@pytest.mark.anyio
async def test_approval_blocks_formal_pdf_until_manager_review(db, monkeypatch):
    app = FastAPI()
    quotes = QuoteService(db, Ledger(db))
    app.include_router(build_quote_router(quotes))
    from forwarder import quotes as quote_module

    was_rendered = []

    def fake_pdf(record):
        was_rendered.append(record["id"])
        return b"%PDF-1.4\n"

    monkeypatch.setattr(quote_module, "_public_quote_pdf", fake_pdf)
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        preview = await client.post("/api/quotes/calculate", json=request(discount_pct=15))
        assert preview.raise_for_status().json()["approval_required"]
        assert (await client.get("/api/quotes")).json()["records"] == []
        draft = (await client.post("/api/quotes", json=request(discount_pct=15))).raise_for_status().json()
        path = f"/api/quotes/{draft['id']}"
        assert draft["status"] == "draft"
        assert (await client.get(path + "/pdf")).status_code == 409
        assert (await client.post(path + "/review", json={"decision": "approved"})).status_code == 409
        pending = (await client.post(path + "/confirm", json={})).raise_for_status().json()
        assert pending["status"] == "pending_approval"
        assert (await client.get(path + "/pdf")).status_code == 409
        formal = (await client.post(path + "/review", json={"decision": "approved"})).raise_for_status().json()
        assert formal["status"] == "formal"
        assert (await client.post(path + "/review", json={"decision": "rejected"})).status_code == 409
        pdf = (await client.get(path + "/pdf")).raise_for_status()
        assert pdf.content.startswith(b"%PDF") and was_rendered == [draft["id"]]
        quotes.ledger.clear()
        assert (await client.get("/api/quotes")).json()["records"] == []


def test_claim_scoping_and_mock_quote_intent(db, world):
    ledger = Ledger(db)
    service = QuoteService(db, ledger)
    registry = ToolRegistry()
    register_quote_tools(registry, service)
    context = ToolContext("T-100", "C-CN-2207", "R-100", WorldView(world, []), ledger)
    wrong = registry.execute("claim.open", {"hbl": "FWQD2611043", "amount_usd": 760, "reason": "外箱进水"},
                             context, set(QUOTE_TOOLS))
    assert not wrong.ok and wrong.error == "not_found"
    claim_request = quote_mock_request("申请理赔 FWQD2611127，理赔金额 USD 760，原因：外箱进水。", "zh")
    claim = registry.execute(*claim_request, context, set(QUOTE_TOOLS))
    assert claim.ok and claim.data["approval_required"]
    assert service.get(claim.data["id"])["status"] == "draft"
    ledger.commit("R-100")
    assert service.get(claim.data["id"])["status"] == "pending_approval"
    mock = quote_mock_request("青岛到仁川海运整柜报价，货物：家居用品，明天出运，FOB，2×40HQ。", "zh")
    assert mock[0] == "quote.create" and mock[1]["container_count"] == 2


@pytest.mark.anyio
async def test_quote_email_runs_through_real_ticket_approval_and_pdf(settings):
    app = create_app(replace(settings, llm_provider="mock", auto_reply_readonly=True), executor=InlineExecutor())
    identity = {"customer_id": "C-CN-2207", "channel": "email"}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            incoming = await client.post("/api/chat/messages", json={
                **identity, "text": "青岛到仁川海运整柜报价，货物：家居用品，明天出运，FOB，2×40HQ，折扣 15%。",
            })
            ticket_id = incoming.raise_for_status().json()["ticket_id"]
            ticket = (await client.get(f"/api/tickets/{ticket_id}")).raise_for_status().json()
            assert ticket["ticket"]["status"] == "awaiting_confirmation"
            assert ticket["runs"][-1]["write_calls"] == ["quote.create"]
            draft = next(message for message in ticket["messages"] if message["status"] == "draft")
            assert "1789.25" in draft["text"] and "经理审核" in draft["text"]
            [record] = (await client.get("/api/quotes", params={"ticket_id": ticket_id})).raise_for_status().json()["records"]
            base = f"/api/quotes/{record['id']}"
            assert record["status"] == "draft" and not record["confirm_in_quotes"]
            assert (await client.post(base + "/confirm", json={})).status_code == 409
            confirmation = await client.post(f"/api/tickets/{ticket_id}/messages/{draft['id']}/confirm", json={})
            assert confirmation.raise_for_status().json()["status"] == "sent"
            assert (await client.get(base + "/pdf")).status_code == 409
            final = (await client.post(base + "/review", json={"decision": "approved"})).raise_for_status().json()
            assert final["status"] == "formal"
            response = (await client.get(base + "/pdf")).raise_for_status()
            assert response.headers["content-type"] == "application/pdf"
            pdf = PdfReader(BytesIO(response.content))
            assert len(pdf.pages) == 1
            # Embedded-font and standard CID-font builds both preserve the quotation identifier.
            assert record["id"] in pdf.pages[0].extract_text()
