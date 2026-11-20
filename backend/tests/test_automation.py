from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from forwarder.automation import MailAutomation, MailInput
from forwarder.main import create_app
from forwarder.tickets.executor import InlineExecutor


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_local_mail_waits_for_employee_and_records_exactly_one_reply(settings):
    settings = replace(settings, llm_provider="mock", auto_reply_readonly=True)
    app = create_app(settings, executor=InlineExecutor())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = await client.post("/api/automation/test-email", json={
                "transport": "local", "customer_id": "C-KR-0412",
                "sender": "sample@example.invalid", "subject": "通关进度",
                "text": "请问 FWQD2611043 的清关进度？",
            })
            assert created.status_code == 201
            job = created.json()
            assert job["status"] == "awaiting_confirmation"
            assert (await client.get("/api/automation")).json()["outbox"] == []
            ticket = (await client.get(f"/api/tickets/{job['ticket_id']}")).json()
            draft = next(message for message in ticket["messages"] if message["status"] == "draft")
            confirmation = f"/api/tickets/{job['ticket_id']}/messages/{draft['id']}/confirm"
            assert (await client.post(confirmation, json={"text": "已核对，等待海关处理。"})).status_code == 200
            assert (await client.post(confirmation, json={})).status_code == 409
            history = (await client.get("/api/automation")).json()
            assert history["records"][0]["status"] == "sent"
            assert len(history["outbox"]) == len(history["customer_log"]) == 1
            assert history["outbox"][0]["body"] == "已核对，等待海关处理。"
            assert history["outbox"][0]["recipient"] == "sample@example.invalid"
            await client.post("/api/demo/reset")
            assert (await client.get("/api/automation")).json() == {
                "records": [], "outbox": [], "customer_log": [],
            }


def test_n8n_resume_requires_a_confirmed_message_and_delivers_idempotently(db, monkeypatch):
    ready = Event()
    calls = []
    transport = httpx.MockTransport(lambda request: _record_resume(request, calls, ready))
    reply = SimpleNamespace(ticket_id="T-001042", id=12, text="已检查", sent_at="2026-11-20T12:00:00+09:00")
    service = SimpleNamespace(customer_message=lambda **kwargs: (SimpleNamespace(id="T-001042"), SimpleNamespace(id=10)))
    repo = SimpleNamespace(messages=lambda ticket_id: [])
    world = SimpleNamespace(customers={"C-KR-0412": {}})
    automation = MailAutomation(db=db, repo=repo, service=service, world=world, http_transport=transport)
    payload = MailInput(
        customer_id="C-KR-0412", sender="a@example.invalid", subject="测试", text="请查货",
        message_id="fixed-id",
    )
    try:
        with pytest.raises(ValueError):
            automation.intake(payload, resume_url="http://localhost:9999/private")
        with pytest.raises(ValueError):
            automation.intake(payload, resume_url="http://127.0.0.1:5678/webhook-waiting/20?unexpected=1")
        signed_url = "http://127.0.0.1:5678/webhook-waiting/20?signature=test.example-token"
        job, token, wait = automation.intake(payload, resume_url=signed_url)
        duplicate, duplicate_token, duplicate_wait = automation.intake(
            payload, resume_url="http://127.0.0.1:5678/webhook-waiting/21",
        )
        assert (duplicate["id"], duplicate_token) == (job["id"], token)
        assert wait is True and duplicate_wait is False
        with pytest.raises(ValueError):
            automation.complete(job["id"], token)
        with pytest.raises(PermissionError):
            automation.complete(job["id"], "无效")
        automation.on_confirmation(reply)
        assert ready.wait(3)
        assert calls == [signed_url]
        assert automation.complete(job["id"], token) == automation.complete(job["id"], token)
        assert len(automation.history()["outbox"]) == 1
    finally:
        automation.close()


def _record_resume(request, calls, ready):
    calls.append(str(request.url))
    ready.set()
    return httpx.Response(200)
