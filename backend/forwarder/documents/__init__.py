"""Staff document reviews and the ticket-scoped read tool."""

import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from forwarder.config import Settings
from forwarder.db import Database
from forwarder.tickets.models import now_iso
from forwarder.tickets.repo import TicketRepo
from forwarder.tools.registry import ToolContext, ToolError, ToolRegistry, ToolSpec

from .analysis import DOCUMENT_LABELS, KINDS, review_documents
from .extraction import MAX_FILE_BYTES, DocumentError, ExtractionMode, read_fields
from .samples import sample_file

DOCUMENT_TOOL = "document.get_findings"
SampleKind = Literal["invoice", "packing", "bill", "photo"]
DraftKind = Literal["correction", "customs"]


class _MissingReview(LookupError):
    pass


class _ReviewConflict(ValueError):
    pass


class DocumentStore:
    def __init__(self, db: Database, tickets: TicketRepo, settings: Settings) -> None:
        self._db = db
        self._tickets = tickets
        self._settings = settings
        self._epoch = 0
        db.execute("""CREATE TABLE IF NOT EXISTS document_reviews (
            id TEXT PRIMARY KEY, ticket_id TEXT, customer_id TEXT, created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS document_reviews_ticket ON document_reviews(ticket_id, created_at)")

    @property
    def deepseek_available(self) -> bool:
        return self._settings.llm_provider == "deepseek" and bool(self._settings.deepseek_api_key)

    def clear(self) -> None:
        with self._db.transaction():
            self._db.execute("DELETE FROM document_reviews")
            self._epoch += 1

    def create(self, files: dict[str, tuple[str, bytes]], *, ticket_id: str | None, mode: ExtractionMode) -> dict:
        if mode == "deepseek" and not self.deepseek_available:
            raise DocumentError("未配置 DeepSeek，请选择本地提取。")
        with self._db.transaction():
            epoch = self._epoch
            ticket = self._tickets.get_ticket(ticket_id) if ticket_id else None
            if ticket_id and ticket is None:
                raise _MissingReview("关联工单不存在。")

        extracted = {}
        for kind in KINDS:
            name, contents = files[kind]
            try:
                method, fields = read_fields(contents, mode, self._settings)
            except DocumentError as exc:
                raise DocumentError(f"{DOCUMENT_LABELS[kind]}：{exc}") from exc
            basename = name.replace(chr(92), "/").rsplit("/", 1)[-1]
            filename = "".join(char for char in basename if char.isprintable())[:160]
            extracted[kind] = {"filename": filename or DOCUMENT_LABELS[kind], "method": method, "fields": fields}

        review = {
            "id": "DOC-" + uuid4().hex[:12].upper(),
            "ticket_id": ticket_id,
            "created_at": now_iso(),
            "documents": extracted,
            **review_documents({kind: record["fields"] for kind, record in extracted.items()}),
        }
        with self._db.transaction():
            if epoch != self._epoch:
                raise _ReviewConflict("演示数据已重置，请重新核对。")
            self._db.execute(
                "INSERT INTO document_reviews(id,ticket_id,customer_id,created_at,payload) VALUES (?,?,?,?,?)",
                (review["id"], ticket_id, ticket.customer_id if ticket else None, review["created_at"],
                 json.dumps(review, ensure_ascii=False)),
            )
        return review

    def demo(self, *, use_photo: bool = False, ticket_id: str | None = None) -> dict:
        sources = {kind: sample_file("photo" if use_photo and kind == "invoice" else kind) for kind in KINDS}
        return self.create(
            {kind: (name, contents) for kind, (name, contents, _) in sources.items()},
            ticket_id=ticket_id, mode="local",
        )

    def list(self) -> list[dict]:
        rows = self._db.query("SELECT payload FROM document_reviews ORDER BY created_at DESC,rowid DESC LIMIT 40")
        result = []
        for row in rows:
            data = json.loads(row["payload"])
            result.append({
                "id": data["id"], "ticket_id": data["ticket_id"], "created_at": data["created_at"],
                "differences": sum(check["status"] == "mismatch" for check in data["checks"]),
            })
        return result

    def get(self, review_id: str) -> dict:
        row = self._db.query_one("SELECT payload FROM document_reviews WHERE id = ?", (review_id,))
        if row is None:
            raise _MissingReview("找不到这次单据核对。")
        return json.loads(row["payload"])

    def findings_for(self, context: ToolContext, review_id: str | None) -> dict:
        query = "SELECT payload FROM document_reviews WHERE ticket_id = ? AND customer_id = ?"
        params = [context.ticket_id, context.customer_id]
        if review_id:
            query += " AND id = ?"
            params.append(review_id)
        row = self._db.query_one(query + " ORDER BY created_at DESC, rowid DESC LIMIT 1", params)
        if row is None:
            raise ToolError("not_found", "没有找到本工单关联的单据核对结果。")
        review = json.loads(row["payload"])
        return {key: review[key] for key in ("id", "checks", "missing", "requirements")}

    def confirm(self, review_id: str, draft_kind: DraftKind, text: str) -> dict:
        text = text.strip()
        if not text:
            raise DocumentError("草稿内容不能为空。")
        with self._db.transaction():
            review = self.get(review_id)
            draft = review["drafts"][draft_kind]
            if draft["status"] != "draft":
                raise _ReviewConflict("该草稿已确认。")
            draft.update(text=text, status="confirmed", confirmed_at=now_iso())
            self._db.execute(
                "UPDATE document_reviews SET payload = ? WHERE id = ?",
                (json.dumps(review, ensure_ascii=False), review_id),
            )
        return review


def register_document_tool(registry: ToolRegistry, store: DocumentStore) -> None:
    registry.register(ToolSpec(
        name=DOCUMENT_TOOL,
        description="读取当前工单关联的单据核对结果，包括收货人、件数、毛重、体积、HS 编码和金额差异。"
        "用于客户询问核对结果。预警是待核查提示；不要声称已更正、申报或通过审核。",
        parameters={
            "type": "object",
            "properties": {"review_id": {"type": "string", "pattern": "^DOC-[A-F0-9]{12}$"}},
            "additionalProperties": False,
        },
        kind="read",
        handler=lambda args, context: store.findings_for(context, args.get("review_id")),
    ))


class DemoReview(BaseModel):
    use_photo: bool = False
    ticket_id: str | None = Field(default=None, max_length=32)


class ConfirmDraft(BaseModel):
    text: str = Field(min_length=1, max_length=6000)


def _http(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except _MissingReview as exc:
        raise HTTPException(404, str(exc)) from exc
    except _ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except DocumentError as exc:
        raise HTTPException(422, str(exc)) from exc


def build_documents_router(store: DocumentStore) -> APIRouter:
    router = APIRouter(prefix="/api/documents", tags=["documents"])

    @router.get("/samples")
    def samples():
        kinds = (*KINDS, "photo")
        labels = {**DOCUMENT_LABELS, "photo": "商业发票照片"}
        return {
            "samples": [{"kind": kind, "label": labels[kind], "url": f"/api/documents/samples/{kind}"} for kind in kinds],
            "deepseek_available": store.deepseek_available,
        }

    @router.get("/samples/{kind}")
    def download_sample(kind: SampleKind):
        filename, contents, media_type = sample_file(kind)
        return Response(contents, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/reviews")
    def list_reviews():
        return {"reviews": store.list()}

    @router.get("/reviews/{review_id}")
    def review(review_id: str):
        return _http(store.get, review_id)

    @router.post("/reviews", status_code=201)
    async def create_review(
        invoice: UploadFile = File(),
        packing: UploadFile = File(),
        bill: UploadFile = File(),
        ticket_id: str | None = Form(default=None, max_length=32),
        mode: ExtractionMode = Form(default="local"),
    ):
        uploads = {"invoice": invoice, "packing": packing, "bill": bill}
        try:
            files = {
                kind: (upload.filename or "", await upload.read(MAX_FILE_BYTES + 1))
                for kind, upload in uploads.items()
            }
        finally:
            for upload in uploads.values():
                await upload.close()
        return await run_in_threadpool(_http, store.create, files, ticket_id=ticket_id, mode=mode)

    @router.post("/demo", status_code=201)
    def demo_review(body: DemoReview):
        return _http(store.demo, use_photo=body.use_photo, ticket_id=body.ticket_id)

    @router.post("/reviews/{review_id}/drafts/{kind}/confirm")
    def approve_draft(review_id: str, kind: DraftKind, body: ConfirmDraft):
        return _http(store.confirm, review_id, kind, body.text)

    return router
