"""Durable simulated mail intake, workbench approval and n8n callback."""

import hmac
import os
import re
import secrets
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from forwarder.db import Database
from forwarder.privacy import mask_pccc
from forwarder.tickets.models import Message, now_iso
from forwarder.tickets.repo import TicketRepo
from forwarder.tickets.service import TicketService
from forwarder.world.data import WorldData


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS automation_mail (
        id TEXT PRIMARY KEY, message_id TEXT UNIQUE NOT NULL, customer_id TEXT NOT NULL,
        sender TEXT NOT NULL, subject TEXT NOT NULL, intent TEXT NOT NULL, transport TEXT NOT NULL,
        resume_url TEXT, token TEXT NOT NULL, ticket_id TEXT UNIQUE, inbound_id INTEGER,
        approved_message_id INTEGER, approved_text TEXT, created_at TEXT NOT NULL,
        approved_at TEXT, sent_at TEXT, delivery_error TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS automation_outbox (
        mail_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, recipient TEXT NOT NULL,
        subject TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS automation_customer_log (
        mail_id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, customer_id TEXT NOT NULL,
        action TEXT NOT NULL, created_at TEXT NOT NULL
    )""",
)
_WAIT_PATH = re.compile(r"/webhook-waiting/\d+/?\Z")
_WAIT_SIGNATURE = re.compile(r"signature=[A-Za-z0-9._~%=-]{1,1800}\Z")
_ADDRESS = re.compile(r"[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+\Z")


class MailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_id: str = Field(min_length=1, max_length=100)
    sender: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=180)
    text: str = Field(min_length=1, max_length=4000)
    message_id: str = Field(default_factory=lambda: f"demo-{uuid.uuid4().hex}", min_length=1, max_length=240)

    @field_validator("sender")
    @classmethod
    def sender_address(cls, value: str) -> str:
        if not _ADDRESS.fullmatch(value):
            raise ValueError("请输入单个有效邮箱地址")
        return value

    @field_validator("subject")
    @classmethod
    def single_line_subject(cls, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError("邮件主题应为一行")
        return value


class TestMailInput(MailInput):
    transport: Literal["local", "n8n"] = "local"


class WebhookMailInput(MailInput):
    resume_url: str = Field(min_length=1, max_length=2048)


class MailCompletion(BaseModel):
    mail_id: str = Field(min_length=1, max_length=80)
    delivery_token: str = Field(min_length=1, max_length=120)


def _intent(text: str) -> str:
    """A display tag for incoming mail. The existing agent still selects the tools."""
    rules = (
        ("quote", r"询价|报价|运价|견적|운임|quote|quotation"),
        ("claim", r"理赔|索赔|보상|클레임|claim"),
        ("customs", r"清关|通关|海关|통관|세관|customs"),
        ("warehouse", r"仓库|破损|창고|파손|warehouse"),
        ("tracking", r"查货|物流|运输|배송|운송|tracking"),
    )
    return next((name for name, pattern in rules if re.search(pattern, text, re.IGNORECASE)), "general")


class MailAutomation:
    def __init__(
        self, *, db: Database, repo: TicketRepo, service: TicketService, world: WorldData,
        http_transport: httpx.BaseTransport | None = None, n8n_url: str | None = None,
    ) -> None:
        self._db = db
        self._repo = repo
        self._service = service
        self._world = world
        self._intake_lock = threading.RLock()
        self._http_transport = http_transport
        self._base = (n8n_url or os.getenv("N8N_DEMO_URL") or "http://127.0.0.1:5678").rstrip("/")
        base = urlsplit(self._base)
        if (
            base.scheme != "http" or base.hostname not in ("127.0.0.1", "localhost", "::1", "n8n")
            or base.username is not None or base.path or base.query or base.fragment
        ):
            raise ValueError("N8N_DEMO_URL must be the local n8n HTTP origin")
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mail-resume")
        with db.transaction():
            for statement in _SCHEMA:
                db.execute(statement)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            transport=self._http_transport, timeout=3.0, trust_env=False, follow_redirects=False,
        )

    def _validate_resume(self, value: str) -> None:
        parsed = urlsplit(value)
        expected = urlsplit(self._base)
        local_names = {"localhost", "127.0.0.1", "::1"}
        same_host = parsed.hostname == expected.hostname or (
            parsed.hostname in local_names and expected.hostname in local_names
        )
        if (
            parsed.scheme != expected.scheme or not same_host or parsed.port != expected.port
            or parsed.username is not None or parsed.fragment
            or not _WAIT_PATH.fullmatch(parsed.path)
            or (parsed.query and not _WAIT_SIGNATURE.fullmatch(parsed.query))
        ):
            raise ValueError("等待地址必须属于本机 n8n")

    def submit_test(self, payload: TestMailInput) -> dict:
        if payload.customer_id not in self._world.customers:
            raise KeyError("客户不存在")
        if payload.transport == "local":
            return self.intake(payload)[0]
        try:
            with self._client() as client:
                response = client.post(
                    f"{self._base}/webhook/forwarder-mail",
                    json=payload.model_dump(exclude={"transport"}),
                )
                response.raise_for_status()
        except httpx.HTTPError:
            # The webhook may have accepted the mail and timed out before returning.
            existing = self._db.query_one("SELECT * FROM automation_mail WHERE message_id = ?", (payload.message_id,))
            if existing and existing["ticket_id"]:
                return self._public(existing)
            raise ConnectionError("无法连接 n8n。请先启动并发布邮件工作流。") from None
        existing = self._db.query_one("SELECT * FROM automation_mail WHERE message_id = ?", (payload.message_id,))
        if not existing or not existing["ticket_id"]:
            raise ConnectionError("n8n 尚未登记来信，请查看工作流执行记录。")
        return self._public(existing)

    def intake(self, payload: MailInput, *, resume_url: str | None = None) -> tuple[dict, str, bool]:
        if payload.customer_id not in self._world.customers:
            raise KeyError("客户不存在")
        if resume_url is not None:
            self._validate_resume(resume_url)
        with self._intake_lock:
            existing = self._db.query_one("SELECT * FROM automation_mail WHERE message_id = ?", (payload.message_id,))
            if existing:
                # Idempotent n8n deliveries return the existing job. A second execution cannot take
                # over a callback registered by the first or obtain a different mailbox's token.
                if (existing["customer_id"], existing["sender"], existing["subject"]) != (
                    payload.customer_id, payload.sender, mask_pccc(payload.subject),
                ):
                    raise ValueError("这封来信的标识已被其他来信使用")
                should_wait = bool(resume_url and resume_url == existing["resume_url"] and not existing["sent_at"])
                return self._public(existing), existing["token"], should_wait

            mail_id = f"M-{uuid.uuid4().hex[:12]}"
            token = secrets.token_urlsafe(32)
            created = now_iso()
            subject = mask_pccc(payload.subject)
            text = f"邮件主题：{subject}\n\n{payload.text}"
            self._db.execute(
                """INSERT INTO automation_mail
                   (id, message_id, customer_id, sender, subject, intent, transport, resume_url, token, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    mail_id, payload.message_id, payload.customer_id, payload.sender, subject,
                    _intent(text), "n8n" if resume_url else "local", resume_url, token, created,
                ),
            )
            try:
                ticket, inbound = self._service.customer_message(
                    customer_id=payload.customer_id, channel="email", text=text,
                    new_ticket=True, subject=subject, force_draft=True,
                )
            except Exception:
                self._db.execute("DELETE FROM automation_mail WHERE id = ?", (mail_id,))
                raise
            self._db.execute(
                "UPDATE automation_mail SET ticket_id = ?, inbound_id = ? WHERE id = ?",
                (ticket.id, inbound.id, mail_id),
            )
            # An approval could have landed while a fast run was registering its new ticket.
            for message in self._repo.messages(ticket.id):
                if message.id > inbound.id and message.role == "agent" and message.kind == "normal" and message.status == "sent":
                    self.on_confirmation(message)
                    break
            return self._public(self._job(mail_id)), token, resume_url is not None

    def on_confirmation(self, message: Message) -> None:
        """Called by TicketService after confirmation commits and its lock is released."""
        with self._intake_lock, self._db.transaction():
            job = self._db.query_one(
                "SELECT * FROM automation_mail WHERE ticket_id = ?", (message.ticket_id,),
            )
            if not job or job["approved_message_id"] is not None or message.id <= job["inbound_id"]:
                return
            self._db.execute(
                """UPDATE automation_mail SET approved_message_id = ?, approved_text = ?, approved_at = ?
                   WHERE id = ?""",
                (message.id, mask_pccc(message.text), message.sent_at or now_iso(), job["id"]),
            )
            if job["transport"] == "local":
                self._finish(self._job(job["id"]))
                return
        self._workers.submit(self._resume, job["id"])

    def _resume(self, mail_id: str) -> None:
        job = self._db.query_one("SELECT * FROM automation_mail WHERE id = ?", (mail_id,))
        if not job or not job["approved_at"] or job["sent_at"]:
            return
        # The webhook may reply before its Wait node has registered. Briefly retry that window.
        try:
            self._validate_resume(job["resume_url"])
            with self._client() as client:
                for delay in (0, 0.2, 0.55, 1.0):
                    if delay:
                        time.sleep(delay)
                    current = self._db.query_one("SELECT sent_at FROM automation_mail WHERE id = ?", (mail_id,))
                    if not current or current["sent_at"]:
                        return
                    response = client.post(job["resume_url"], json={"mail_id": mail_id})
                    if response.is_success:
                        return
                    if response.status_code not in (404, 409):
                        break
        except (httpx.HTTPError, ValueError):
            pass
        self._db.execute(
            "UPDATE automation_mail SET delivery_error = ? WHERE id = ? AND sent_at IS NULL",
            ("n8n 未恢复等待步骤，可在服务就绪后重试。", mail_id),
        )

    def retry(self, mail_id: str) -> dict:
        job = self._job(mail_id)
        if job["transport"] != "n8n" or not job["approved_at"]:
            raise ValueError("来信尚未完成员工确认")
        if not job["sent_at"]:
            self._db.execute("UPDATE automation_mail SET delivery_error = NULL WHERE id = ?", (mail_id,))
            self._workers.submit(self._resume, mail_id)
        return self._public(self._job(mail_id))

    def complete(self, mail_id: str, delivery_token: str) -> dict:
        with self._db.transaction():
            job = self._job(mail_id)
            if job["transport"] != "n8n" or not hmac.compare_digest(job["token"].encode(), delivery_token.encode()):
                raise PermissionError("邮件回调令牌无效")
            if not job["approved_at"]:
                raise ValueError("来信尚未完成员工确认")
            self._finish(job)
            return self._public(self._job(mail_id))

    def _finish(self, job: dict) -> None:
        if job["sent_at"]:
            return
        now = now_iso()
        self._db.execute(
            "INSERT INTO automation_outbox VALUES (?, ?, ?, ?, ?, ?)",
            (job["id"], job["ticket_id"], job["sender"], f"Re: {job['subject']}", job["approved_text"], now),
        )
        self._db.execute(
            "INSERT INTO automation_customer_log VALUES (?, ?, ?, ?, ?)",
            (job["id"], job["ticket_id"], job["customer_id"], "mock_reply_recorded", now),
        )
        self._db.execute(
            "UPDATE automation_mail SET sent_at = ?, delivery_error = NULL WHERE id = ?", (now, job["id"]),
        )

    def _job(self, mail_id: str) -> dict:
        job = self._db.query_one("SELECT * FROM automation_mail WHERE id = ?", (mail_id,))
        if not job:
            raise KeyError("来信不存在")
        return job

    def _public(self, row: dict) -> dict:
        ticket = self._db.query_one("SELECT status FROM tickets WHERE id = ?", (row["ticket_id"],))
        if row["sent_at"]:
            status = "sent"
        elif row["approved_at"]:
            status = "awaiting_delivery"
        else:
            status = ticket["status"] if ticket else "processing"
        elapsed = (datetime.fromisoformat(row["sent_at"] or now_iso()) - datetime.fromisoformat(row["created_at"]))
        keys = (
            "id", "customer_id", "sender", "subject", "ticket_id", "intent", "transport",
            "created_at", "approved_at", "sent_at", "delivery_error",
        )
        return {**{key: row[key] for key in keys}, "status": status, "elapsed_ms": max(0, int(elapsed.total_seconds() * 1000))}

    def history(self) -> dict:
        with self._db.transaction():
            return {
                "records": [self._public(row) for row in self._db.query(
                    "SELECT * FROM automation_mail ORDER BY created_at DESC, id DESC",
                )],
                "outbox": self._db.query("SELECT * FROM automation_outbox ORDER BY created_at DESC, mail_id DESC"),
                "customer_log": self._db.query(
                    "SELECT * FROM automation_customer_log ORDER BY created_at DESC, mail_id DESC",
                ),
            }

    def reset(self) -> None:
        with self._intake_lock, self._db.transaction():
            self._db.execute("DELETE FROM automation_outbox")
            self._db.execute("DELETE FROM automation_customer_log")
            self._db.execute("DELETE FROM automation_mail")

    def close(self) -> None:
        self._workers.shutdown(wait=True, cancel_futures=True)


def build_automation_router(mail: MailAutomation) -> APIRouter:
    router = APIRouter(prefix="/api/automation")

    @router.get("")
    def history():
        return mail.history()

    @router.post("/test-email", status_code=201)
    def test_email(body: TestMailInput):
        try:
            return mail.submit_test(body)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except ConnectionError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.post("/n8n/intake", status_code=201)
    def webhook(body: WebhookMailInput):
        try:
            record, token, wait = mail.intake(body, resume_url=body.resume_url)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"id": record["id"], "ticket_id": record["ticket_id"], "delivery_token": token, "wait": wait}

    @router.post("/n8n/complete")
    def complete(body: MailCompletion):
        try:
            return mail.complete(body.mail_id, body.delivery_token)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from None
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/{mail_id}/retry")
    def retry(mail_id: str):
        try:
            return mail.retry(mail_id)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
