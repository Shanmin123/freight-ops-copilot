"""REST and event-stream routes for the workbench and the customer chat."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from forwarder.config import Settings
from forwarder.privacy import mask_pccc, mask_pccc_deep
from forwarder.scorecard import audit_reply
from forwarder.tickets.events import Event, EventBus
from forwarder.tickets.models import Channel
from forwarder.tickets.repo import TicketRepo
from forwarder.tickets.service import NotADraftError, TicketService
from forwarder.world.data import WorldData
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView

from .sse import customer_visible, event_stream

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EventHeader = Annotated[int | None, Header(ge=0)]
EventCursor = Annotated[int, Query(ge=0)]


class ChatMessageBody(BaseModel):
    customer_id: str
    channel: Channel
    text: NonEmptyText


class ConfirmBody(BaseModel):
    text: NonEmptyText | None = None


class SettingsBody(BaseModel):
    auto_reply_readonly: bool


def build_router(
    *,
    settings: Settings,
    service: TicketService,
    repo: TicketRepo,
    bus: EventBus,
    ledger: Ledger,
    world: WorldData,
    seed_path: Path,
    reset_extras: tuple[Callable[[], None], ...] = (),
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health():
        deepseek = settings.llm_provider == "deepseek"
        return {
            "status": "ok",
            "llm": {
                "provider": settings.llm_provider,
                "model": settings.agent_model if deepseek else "mock-demo",
                "thinking": deepseek and settings.agent_thinking,
                "fallback_reason": settings.llm_fallback_reason,
            },
            "company": settings.company_display_name,
            "last_seq": bus.last_seq,
        }

    @router.get("/settings")
    def read_settings():
        return SettingsBody(auto_reply_readonly=settings.auto_reply_readonly)

    @router.put("/settings")
    def update_settings(body: SettingsBody):
        settings.auto_reply_readonly = body.auto_reply_readonly
        return body

    def require_customer(customer_id: str) -> None:
        if customer_id not in world.customers:
            raise HTTPException(404, "客户不存在")

    @router.get("/customers")
    def customers():
        fields = ("id", "name", "type", "language")
        return [{field: row[field] for field in fields} for row in world.customers.values()]

    @router.get("/customers/{customer_id}/summary")
    def customer_summary(customer_id: str):
        require_customer(customer_id)
        view = WorldView(world, ledger.effects())
        shipments = [
            {**shipment, "customs": view.customs(shipment["hbl"])}
            for shipment in view.shipments_for(customer_id)
        ]
        return mask_pccc_deep({
            "customer": view.customer(customer_id),
            "shipments": shipments,
            "warehouse_jobs": view.warehouse_jobs_for(customer_id),
        })

    @router.post("/chat/messages")
    def post_message(body: ChatMessageBody):
        require_customer(body.customer_id)
        ticket, message = service.customer_message(**body.model_dump())
        return {"ticket_id": ticket.id, "message": message.to_dict()}

    @router.get("/chat/conversation")
    def conversation(customer_id: str, channel: Channel):
        require_customer(customer_id)
        # A concurrent change after this cursor is replayed, even if the snapshot already contains it.
        last_seq = bus.last_seq
        ticket = repo.latest_ticket_for(customer_id, channel)
        return {
            "ticket_id": ticket.id if ticket is not None else None,
            "messages": [m.to_dict() for m in service.customer_messages(ticket.id)] if ticket is not None else [],
            "last_seq": last_seq,
        }

    def stream_response(
        request: Request, cursor: int, accept: Callable[[Event], bool],
    ) -> StreamingResponse:
        return StreamingResponse(
            event_stream(
                bus,
                since=cursor,
                accept=accept,
                is_disconnected=request.is_disconnected,
                ping_seconds=settings.sse_ping_seconds,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/chat/tickets/{ticket_id}/events")
    def chat_events(
        request: Request, ticket_id: str, since: EventCursor = 0, last_event_id: EventHeader = None,
    ):
        cursor = last_event_id if last_event_id is not None else since
        if repo.get_ticket(ticket_id) is None:
            # A reset may have removed the ticket between the last read and the connection.
            recent = bus.since(0)
            reset = next((e for e in reversed(recent) if e.type == "demo.reset"), None)
            if reset is not None and reset.seq > cursor and any(
                e.ticket_id == ticket_id and e.seq < reset.seq for e in recent
            ):
                return stream_response(request, cursor, lambda event: event.type == "demo.reset")
            raise HTTPException(404, "工单不存在")
        return stream_response(request, cursor, lambda event: customer_visible(event, ticket_id))

    @router.get("/tickets")
    def tickets():
        summaries = []
        for ticket in repo.list_tickets():
            messages = service.customer_messages(ticket.id)
            customer = world.customers.get(ticket.customer_id)
            summaries.append({
                "id": ticket.id,
                "customer_id": ticket.customer_id,
                "customer_name": customer["name"] if customer else ticket.customer_id,
                "channel": ticket.channel,
                "subject": mask_pccc(ticket.subject),
                "status": ticket.status,
                "updated_at": ticket.updated_at,
                "preview": mask_pccc(messages[-1].text)[:60] if messages else "",
            })
        return summaries

    @router.get("/tickets/{ticket_id}")
    def ticket_detail(ticket_id: str):
        last_seq = bus.last_seq
        try:
            detail = service.ticket_detail(ticket_id)
        except KeyError:
            raise HTTPException(404, "工单不存在") from None
        latest = detail["runs"][-1] if detail["runs"] else None
        quality = None
        if latest and latest["status"] == "finished":
            answer = next((message["text"] for message in reversed(detail["messages"]) if
                           message["run_id"] == latest["id"] and message["kind"] == "normal"
                           and message["status"] in ("sent", "draft")), "")
            problems = audit_reply(answer, latest["steps"])
            quality = {"points": max(0, 100 - len(problems) * 35), "problems": problems}
        return {**detail, "quality": quality, "last_seq": last_seq}

    @router.post("/tickets/{ticket_id}/messages/{message_id}/confirm")
    def confirm_draft(ticket_id: str, message_id: int, body: ConfirmBody | None = None):
        try:
            message = service.confirm_draft(ticket_id, message_id, text=body.text if body else None)
        except KeyError:
            raise HTTPException(404, "工单或消息不存在") from None
        except NotADraftError:
            raise HTTPException(409, "这条消息已不在待确认状态") from None
        return message.to_dict()

    @router.post("/tickets/{ticket_id}/messages/{message_id}/regenerate", status_code=202)
    def regenerate_draft(ticket_id: str, message_id: int):
        try:
            service.regenerate_draft(ticket_id, message_id)
        except KeyError:
            raise HTTPException(404, "工单或消息不存在") from None
        except NotADraftError:
            raise HTTPException(409, "这条消息已不在待确认状态") from None
        return Response(status_code=202)

    @router.get("/events")
    def workbench_events(request: Request, since: EventCursor = 0, last_event_id: EventHeader = None):
        return stream_response(request, last_event_id if last_event_id is not None else since, lambda _: True)

    @router.post("/demo/reset")
    def reset_demo():
        for reset in reset_extras:
            reset()
        service.reset_demo(seed_path)
        return {"ok": True}

    return router
