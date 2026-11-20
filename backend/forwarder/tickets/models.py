"""Ticket records: tickets, their messages, and the agent runs that answered them."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal, get_args

from forwarder.world.data import WORLD_TZ

TicketStatus = Literal["processing", "ai_replied", "awaiting_confirmation", "replied", "needs_human"]
Channel = Literal["web", "wechat", "kakaotalk", "email", "web_form"]
MessageRole = Literal["customer", "agent"]
MessageKind = Literal["normal", "ack"]  # ack: the automatic "we got your message, please wait" reply
MessageStatus = Literal["sent", "draft", "discarded"]
RunStatus = Literal["running", "finished"]
# What became of a run's reply: sent at once, kept as a draft for staff, or nothing (error, or dropped).
RunDecision = Literal["auto_sent", "draft", "none"]

TICKET_STATUSES: tuple[str, ...] = get_args(TicketStatus)
CHANNELS: tuple[str, ...] = get_args(Channel)


def now_iso() -> str:
    """The current time as the records store it: ISO 8601, to the millisecond, at the demo world's +09:00."""
    return datetime.now(WORLD_TZ).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Ticket:
    id: str  # "T-" + 6 digits
    customer_id: str
    channel: Channel
    subject: str
    status: TicketStatus
    created_at: str
    updated_at: str  # the last change to the ticket or to any of its messages

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Message:
    id: int
    ticket_id: str
    role: MessageRole
    kind: MessageKind
    text: str
    status: MessageStatus
    run_id: str | None  # the run that wrote it; None for customer messages
    edited: bool  # staff changed the text before sending it
    created_at: str
    sent_at: str | None  # None until it is sent

    @property
    def visible_to_customer(self) -> bool:
        return self.status == "sent"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RunRecord:
    id: str
    ticket_id: str
    status: RunStatus
    outcome: str | None  # the run's outcome, e.g. "final"; None while running
    decision: RunDecision | None  # None while running
    model: str
    steps: list[dict]
    usage: dict
    write_calls: list[str]
    error: str | None
    started_at: str
    finished_at: str | None  # None while running

    def to_dict(self) -> dict:
        return asdict(self)
