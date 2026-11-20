"""What a run starts from: the system prompt, and the ticket written out as one user message."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from forwarder.language import ReplyLanguage, detect_reply_language

from forwarder.world.data import WORLD_TZ

# Found next to this file, so the working directory does not matter.
_PROMPT_FILE = Path(__file__).parent / "prompts" / "system_logistics.md"

# Transcript role -> how the user message names the speaker.
_SPEAKERS = {"customer": "客户", "agent": "客服"}


@dataclass(frozen=True)
class TicketContext:
    """The ticket as a run sees it.

    transcript holds the earlier messages, oldest first, as (role, text) with role "customer" or
    "agent"; latest_message is the customer message the run answers.
    """

    ticket_id: str
    channel: str
    customer_id: str
    customer_name: str
    transcript: list[tuple[str, str]]
    latest_message: str
    customer_language: ReplyLanguage = "zh"

    @property
    def reply_language(self) -> ReplyLanguage:
        earlier = (text for role, text in reversed(self.transcript) if role == "customer")
        return detect_reply_language((self.latest_message, *earlier), fallback=self.customer_language)


def load_system_prompt(company: str) -> str:
    """The logistics system prompt, read as UTF-8, with every {company} replaced by company."""
    # replace, not str.format: neither the prompt nor a company name has to avoid braces.
    prompt = _PROMPT_FILE.read_text(encoding="utf-8").replace("{company}", company)
    return prompt.replace("{today}", datetime.now(WORLD_TZ).date().isoformat())


def build_messages(ctx: TicketContext, company: str) -> list[dict]:
    """The two messages a run opens with: the system prompt, then the ticket as text.

    The earlier conversation goes in as text, never as assistant messages to replay: with thinking
    on, a replayed assistant message would need its reasoning_content, which only lives in its run.
    ValueError for a transcript role other than "customer" or "agent".
    """
    language = "韩文（한국어）" if ctx.reply_language == "ko" else "中文"
    return [
        {"role": "system", "content": load_system_prompt(company) + f"\n本轮客服回复语言为{language}。"},
        {"role": "user", "content": _ticket_text(ctx)},
    ]


def _ticket_text(ctx: TicketContext) -> str:
    transcript = [f"{_speaker(role)}：{text}" for role, text in ctx.transcript]
    lines = [
        f"【工单】{ctx.ticket_id} · 渠道 {ctx.channel} · 客户编号 {ctx.customer_id}（{ctx.customer_name}）",
        "【此前对话】",
        *(transcript or ["无"]),
        "【客户最新消息】",
        ctx.latest_message,
    ]
    return "\n".join(lines)


def _speaker(role: str) -> str:
    speaker = _SPEAKERS.get(role)
    if speaker is None:
        raise ValueError(f"transcript role must be 'customer' or 'agent', not {role!r}")
    return speaker
