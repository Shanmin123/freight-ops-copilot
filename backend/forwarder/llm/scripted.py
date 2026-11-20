"""ScriptedModel, a ChatModel for tests, and helpers that build replies the way OpenAICompatModel does."""

import copy
import json
from collections import deque

from .base import ModelReply, ToolCall, to_wire_name, wire_assistant_message


class ScriptedModel:
    """Returns the scripted replies in order; an Exception in the script is raised instead.

    calls holds a deep copy of the messages of every call, including calls that raised.
    """

    def __init__(self, replies: list[ModelReply | Exception], name: str = "scripted") -> None:
        self.name = name
        self.calls: list[list[dict]] = []
        self._replies = deque(replies)

    def complete(self, messages: list[dict], tools: list[dict] | None) -> ModelReply:
        self.calls.append(copy.deepcopy(messages))
        if not self._replies:
            raise AssertionError(f"ScriptedModel: no scripted reply left for call {len(self.calls)}")
        reply = self._replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        # Copy per call, like the real client: one reply object may sit in the script several times.
        return copy.deepcopy(reply)


def reply_text(text: str) -> ModelReply:
    return _reply(text, [])


def reply_tool(
    name: str, arguments: dict, call_id: str = "call_1", reasoning: str | None = None
) -> ModelReply:
    return _reply("", [ToolCall(call_id, name, arguments)], reasoning)


def reply_tools(calls: list[tuple[str, dict]]) -> ModelReply:
    """One reply carrying several tool calls, with ids call_1, call_2, ..."""
    tool_calls = [ToolCall(f"call_{i}", name, args) for i, (name, args) in enumerate(calls, start=1)]
    return _reply("", tool_calls)


def _reply(content: str, tool_calls: list[ToolCall], reasoning: str | None = None) -> ModelReply:
    wire_calls = [
        (call.id, to_wire_name(call.name), json.dumps(call.arguments, ensure_ascii=False))
        for call in tool_calls
    ]
    return ModelReply(
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning,
        usage={},
        model="scripted",
        wire_message=wire_assistant_message(content, wire_calls, reasoning),
    )
