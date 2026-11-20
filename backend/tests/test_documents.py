from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from forwarder.db import Database
from forwarder.documents import DOCUMENT_TOOL, DocumentStore, build_documents_router, register_document_tool
from forwarder.documents.analysis import extract_fields, review_documents
from forwarder.documents.extraction import read_fields
from forwarder.documents.samples import sample_file
from forwarder.tickets.repo import TicketRepo
from forwarder.tools.registry import ToolContext, ToolRegistry
from forwarder.world.ledger import Ledger


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_document_comparison_uses_units_and_does_not_treat_missing_as_zero():
    docs = {
        "invoice": extract_fields("Consignee: Blue Harbor Ltd.\nPackages: 10\nHS Code: 8517.62\nInvoice Total: USD 1,200.00"),
        "packing": extract_fields("收货人：Blue Harbor Ltd\n件数：10\n毛重：50 kg\n体积：0.70 m3"),
        "bill": extract_fields("Consignee: Blue Harbor LTD\nPackages: 10 cartons\nGross Weight: 110.2 LB\nVolume: 710 L\nHS Code: 8517620000"),
    }
    result = review_documents(docs)
    checks = {row["field"]: row for row in result["checks"]}
    assert all(checks[field]["status"] == "match" for field in ("consignee", "packages", "gross_kg", "volume_cbm", "hs_code"))
    assert checks["amount"]["status"] == "single"
    assert checks["amount"]["values"]["bill"] is None
    assert any(item["code"] == "kc_radio" for item in result["requirements"])


@pytest.mark.anyio
async def test_pdf_upload_history_confirmation_and_ticket_permission(settings, tmp_path):
    db = Database(tmp_path / "documents.db")
    db.init_schema()
    repo = TicketRepo(db)
    ticket = repo.create_ticket(customer_id="C-KR-0412", channel="web", subject="Check docs")
    other = repo.create_ticket(customer_id="C-CN-3150", channel="web", subject="Unrelated")
    store = DocumentStore(db, repo, settings)
    app = FastAPI()
    app.include_router(build_documents_router(store))
    registry = ToolRegistry()
    register_document_tool(registry, store)
    context = ToolContext(ticket.id, ticket.customer_id, "R-1", None, Ledger(db))
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        files = {kind: sample_file(kind) for kind in ("invoice", "packing", "bill")}
        response = await client.post("/api/documents/reviews", files=files, data={"ticket_id": ticket.id})
        assert response.status_code == 201, response.text
        report = response.json()
        review_id = report["id"]
        checks = Counter(item["status"] for item in report["checks"])
        assert checks["mismatch"] >= 3
        assert report["drafts"]["correction"]["status"] == "draft"
        assert any(rule["kb_url"] for rule in report["requirements"])
        assert registry.execute(DOCUMENT_TOOL, {"review_id": review_id}, context, {DOCUMENT_TOOL}).ok
        assert registry.execute(DOCUMENT_TOOL, {"review_id": review_id}, replace(context, ticket_id=other.id, customer_id=other.customer_id), {DOCUMENT_TOOL}).error == "not_found"
        confirm = await client.post(f"/api/documents/reviews/{review_id}/drafts/correction/confirm",
                                    json={"text": "Please correct the package count."})
        assert confirm.raise_for_status().json()["drafts"]["correction"]["status"] == "confirmed"
        repeat = await client.post(f"/api/documents/reviews/{review_id}/drafts/correction/confirm",
                                   json={"text": "Replaced"})
        assert repeat.status_code == 409
        rows = (await client.get("/api/documents/reviews")).raise_for_status().json()
        assert rows["reviews"][0]["id"] == review_id
    store.clear()
    assert store.list() == []
    db.close()


def test_photo_really_uses_local_ocr(settings, tmp_path):
    db = Database(tmp_path / "documents.db")
    db.init_schema()
    store = DocumentStore(db, TicketRepo(db), settings)
    review = store.demo(use_photo=True)
    invoice = review["documents"]["invoice"]
    assert invoice["method"] == "image_ocr"
    assert "".join(invoice["fields"]["consignee"].split()) == "BLUEHARBORRETAILLTD"
    assert invoice["fields"]["packages"] == 40
    assert invoice["fields"]["hs_code"] == "851762"
    assert invoice["fields"]["amount"] == 2400 and invoice["fields"]["currency"] == "USD"
    assert {"kc_radio", "fta_origin"} <= {item["code"] for item in review["requirements"]}
    db.close()


def test_opt_in_vision_builds_a_multimodal_request_and_normalizes_the_model_reply(settings, monkeypatch):
    calls = []

    class StubModel:
        def __init__(self, **config):
            assert config["api_key"] == "an-example-key"

        def complete(self, messages, tools):
            calls.append(messages)
            assert tools is None
            return SimpleNamespace(content='{"consignee":"Blue Harbor Ltd","packages":"40","gross_kg":480,"hs_code":"8517.62"}')

    monkeypatch.setattr("forwarder.documents.extraction.OpenAICompatModel", StubModel)
    model_settings = replace(settings, llm_provider="deepseek", deepseek_api_key="an-example-key")
    file = sample_file("photo")[1]
    method, fields = read_fields(file, "deepseek", model_settings)
    assert method == "deepseek_vision" and fields["hs_code"] == "851762" and fields["packages"] == 40
    assert len(calls) == 1
    assert calls[0][-1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
