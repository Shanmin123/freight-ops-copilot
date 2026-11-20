"""Shared by every chat model: ToolCall, ModelReply, LLMError, the ChatModel protocol, the wire format."""

from dataclasses import dataclass
from typing import Protocol


def to_wire_name(name: str) -> str:
    """Dots are not allowed in API tool names: "customs.get_progress" -> "customs__get_progress"."""
    return name.replace(".", "__")


def from_wire_name(name: str) -> str:
    """Inverse of to_wire_name: "customs__get_progress" -> "customs.get_progress"."""
    return name.replace("__", ".")


@dataclass
class ToolCall:
    id: str
    name: str  # dotted form, e.g. "customs.get_progress"
    arguments: dict
    arguments_error: str | None = None  # why the model's arguments were unusable; arguments is {} then


@dataclass
class ModelReply:
    content: str
    tool_calls: list[ToolCall]
    reasoning_content: str | None
    usage: dict  # as the API reported it, extra keys included
    model: str
    wire_message: dict  # assistant message to append to the history as is; tool names stay in "__" form


def wire_assistant_message(
    content: str, tool_calls: list[tuple[str, str, str]], reasoning_content: str | None
) -> dict:
    """Assistant message in the API's own format, for replaying it in the history.

    tool_calls holds (id, wire name, arguments as a JSON string). The "tool_calls" key appears only
    when there are tool calls, and "reasoning_content" only when it is not None.
    """
    message: dict = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
            for call_id, name, arguments in tool_calls
        ]
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return message


class LLMError(Exception):
    """The model API gave no usable reply: error status, unreadable body, or no response (status None)."""

    def __init__(self, status: int | None, body: str) -> None:
        super().__init__(status, body)
        self.status = status
        self.body = body

    def __str__(self) -> str:
        if self.status is None:
            return f"LLM network error: {self.body}"
        return f"LLM HTTP {self.status}: {self.body}"


class ChatModel(Protocol):
    name: str

    def complete(self, messages: list[dict], tools: list[dict] | None) -> ModelReply:
        """Return the model's next assistant message. tools are OpenAI-format specs with dotted names."""
        ...
