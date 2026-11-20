import asyncio
import json
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from forwarder.llm.mock_demo import DemoMockModel
from forwarder.main import create_app
from forwarder.tickets.executor import InlineExecutor, ManualExecutor


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(settings):
    settings = replace(settings, llm_provider="mock", auto_reply_readonly=True)
    app = create_app(settings, executor=InlineExecutor(), model_factory=DemoMockModel)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
            yield client


async def post_message(client, text, customer_id="C-KR-0412", channel="kakaotalk"):
    return await client.post("/api/chat/messages", json={
        "customer_id": customer_id, "channel": channel, "text": text,
    })


async def conversation(client):
    response = await client.get("/api/chat/conversation", params={
        "customer_id": "C-KR-0412", "channel": "kakaotalk",
    })
    assert response.status_code == 200
    return response.json()


async def test_health_never_exposes_key(settings):
    settings = replace(
        settings, llm_provider="deepseek", deepseek_api_key="sk-secret", llm_fallback_reason=None,
        agent_model="deepseek-flash", agent_thinking=True,
    )
    app = create_app(settings, executor=InlineExecutor(), model_factory=DemoMockModel)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
            response = await client.get("/api/health")
    assert "sk-secret" not in response.text
    assert response.json()["llm"] == {
        "provider": "deepseek", "model": "deepseek-flash", "thinking": True, "fallback_reason": None,
    }


async def test_chat_flow_with_mock_model(client):
    assert (await client.get("/api/health")).json()["llm"]["model"] == "mock-demo"
    assert (await conversation(client))["ticket_id"] is None
    ticket_id = (await post_message(client, "FWQD2611043 반입신고에서 멈췄어요")).json()["ticket_id"]
    conv = await conversation(client)
    assert conv["ticket_id"] == ticket_id
    assert [m["role"] for m in conv["messages"]] == ["customer", "agent"]
    assert "개인통관고유부호" in conv["messages"][-1]["text"]

    detail = (await client.get(f"/api/tickets/{ticket_id}")).json()
    assert detail["ticket"]["status"] == "ai_replied"
    assert detail["runs"][0]["steps"]
    assert detail["last_seq"] == conv["last_seq"]


async def test_confirm_endpoint_and_committed_customer_summary(client):
    summary_url = "/api/customers/C-KR-0412/summary"
    await post_message(client, "FWQD2611043 통관 문의")
    ticket_id = (await post_message(client, "P230518447201")).json()["ticket_id"]
    detail = (await client.get(f"/api/tickets/{ticket_id}")).json()
    draft = next(m for m in detail["messages"] if m["status"] == "draft")
    assert draft["id"] not in [m["id"] for m in (await conversation(client))["messages"]]
    shipment = (await client.get(summary_url)).json()["shipments"][0]
    assert shipment["hbl"] == "FWQD2611043"
    assert shipment["customs"]["hold"]["status"] == "open"

    url = f"/api/tickets/{ticket_id}/messages/{draft['id']}/confirm"
    response = await client.post(url, json={})
    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert response.json()["id"] == (await conversation(client))["messages"][-1]["id"]
    assert (await client.post(url, json={})).status_code == 409
    assert (await client.get(summary_url)).json()["shipments"][0]["customs"]["hold"]["status"] == "resubmitted"


async def test_settings_toggle_and_regenerate(client):
    assert (await client.put("/api/settings", json={"auto_reply_readonly": False})).json() == {
        "auto_reply_readonly": False,
    }
    assert (await client.get("/api/settings")).json() == {"auto_reply_readonly": False}
    ticket_id = (await post_message(client, "FWQD2611043 통관 문의")).json()["ticket_id"]
    detail_url = f"/api/tickets/{ticket_id}"
    draft = next(m for m in (await client.get(detail_url)).json()["messages"] if m["status"] == "draft")
    url = f"{detail_url}/messages/{draft['id']}/regenerate"
    assert (await client.post(url)).status_code == 202
    assert (await client.post(url)).status_code == 409
    assert [m["status"] for m in (await client.get(detail_url)).json()["messages"]] == [
        "sent", "sent", "discarded", "draft",
    ]


async def test_validation_errors(client):
    assert (await post_message(client, "hi", customer_id="C-X")).status_code == 404
    assert (await post_message(client, "hi", channel="fax")).status_code == 422
    assert (await post_message(client, "  ")).status_code == 422
    assert (await client.get("/api/tickets/unknown")).status_code == 404
    assert (await client.get("/api/chat/tickets/unknown/events")).status_code == 404
    assert (await client.get("/api/customers/unknown/summary")).status_code == 404
    assert (await client.get("/api/missing")).status_code == 404


async def test_preview_masks_customer_number_while_run_is_queued(settings):
    executor = ManualExecutor()
    app = create_app(settings, executor=executor, model_factory=DemoMockModel)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
            ticket_id = (await post_message(client, "P230518447201 번호입니다")).json()["ticket_id"]
            assert executor.pending == 1
            tickets = (await client.get("/api/tickets")).json()
    latest = next(ticket for ticket in tickets if ticket["id"] == ticket_id)
    assert latest["preview"] == "P2305****7201 번호입니다"


async def test_reset_restores_seed(client):
    seed_ids = {"T-001040", "T-001041"}
    tickets = (await client.get("/api/tickets")).json()
    assert {ticket["id"] for ticket in tickets} == seed_ids
    assert {ticket["channel"] for ticket in tickets} == {"email", "wechat"}
    assert len((await client.get("/api/customers")).json()) == 4
    created = (await post_message(client, "在吗", customer_id="C-CN-3150", channel="web")).json()["ticket_id"]
    assert created not in seed_ids
    assert (await client.post("/api/demo/reset")).json() == {"ok": True}
    assert {ticket["id"] for ticket in (await client.get("/api/tickets")).json()} == seed_ids


async def test_spa_serves_assets_and_falls_back_to_index(settings, tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>Forwarder demo</title>", encoding="utf-8")
    (dist / "demo.css").write_text("body { color: black; }", encoding="utf-8")
    monkeypatch.setattr("forwarder.main.DIST_DIR", dist)
    app = create_app(settings, executor=InlineExecutor(), model_factory=DemoMockModel)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
            assert (await client.get("/workbench")).text == "<title>Forwarder demo</title>"
            assert (await client.get("/chat")).text == "<title>Forwarder demo</title>"
            assert (await client.get("/demo.css")).headers["content-type"].startswith("text/css")
            assert (await client.get("/api/missing")).status_code == 404


async def test_chat_stream_reconnect_receives_reset_after_ticket_was_removed(settings):
    app = create_app(settings, executor=InlineExecutor(), model_factory=DemoMockModel)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
            ticket_id = (await post_message(client, "FWQD2611043 통관 문의")).json()["ticket_id"]
            cursor = (await conversation(client))["last_seq"]
            await client.post("/api/demo/reset")

        disconnected = asyncio.Event()
        responses = []

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            responses.append(message)
            if message["type"] == "http.response.body":
                disconnected.set()

        path = f"/api/chat/tickets/{ticket_id}/events"
        scope = {
            "type": "http", "asgi": {"spec_version": "2.3"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(),
            "query_string": b"since=10000", "headers": [(b"last-event-id", str(cursor).encode())],
        }
        await asyncio.wait_for(app(scope, receive, send), 2)

    assert responses[0]["status"] == 200
    assert (b"cache-control", b"no-cache") in responses[0]["headers"]
    chunk = responses[1]["body"].decode()
    assert "event: demo.reset" in chunk
    event = json.loads(chunk.splitlines()[2][6:])
    assert event["seq"] == cursor + 1
