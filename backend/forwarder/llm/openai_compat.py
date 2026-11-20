"""DeepSeek client over its OpenAI-compatible chat completions API."""

import json

import httpx

from .base import LLMError, ModelReply, ToolCall, from_wire_name, to_wire_name, wire_assistant_message


class OpenAICompatModel:
    """ChatModel that calls POST {base_url}/chat/completions.

    thinking True/False sends DeepSeek's switch {"thinking": {"type": "enabled" | "disabled"}}; None
    leaves the key out so the server default applies. Parameters the API docs do not list, such as
    parallel_tool_calls, are never sent. Every failure to get a usable reply raises LLMError.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        thinking: bool | None = False,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._thinking = thinking
        self._timeout = timeout
        self._transport = transport

    def complete(self, messages: list[dict], tools: list[dict] | None) -> ModelReply:
        payload: dict = {"model": self.name, "messages": messages}
        if tools:  # None and [] both send no tools, never an empty array
            payload["tools"] = [_to_wire_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        if self._thinking is not None:
            payload["thinking"] = {"type": "enabled" if self._thinking else "disabled"}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            # A model lives for one agent run, so a client per call leaves nothing open afterwards.
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(self._url, content=body, headers=self._headers)
        except httpx.RequestError as exc:  # timeouts, refused connections, dropped responses
            raise LLMError(None, _describe(exc)) from exc
        if not response.is_success:
            raise LLMError(response.status_code, response.text)
        try:
            return _reply_from(response.json(), self.name)
        except (ValueError, LookupError, TypeError, AttributeError) as exc:
            raise LLMError(response.status_code, f"unexpected response body: {response.text}") from exc


def _to_wire_tool(tool: dict) -> dict:
    """Copy of an OpenAI-format tool spec with its function name in wire form."""
    function = tool["function"]
    return {**tool, "function": {**function, "name": to_wire_name(function["name"])}}


def _reply_from(data: dict, configured_model: str) -> ModelReply:
    message = data["choices"][0]["message"]
    content = message.get("content") or ""  # null next to tool calls
    reasoning = message.get("reasoning_content")
    calls = [
        (call["id"], call["function"]["name"], call["function"]["arguments"])
        for call in message.get("tool_calls") or []
    ]
    return ModelReply(
        content=content,
        tool_calls=[_tool_call(call_id, wire_name, arguments) for call_id, wire_name, arguments in calls],
        reasoning_content=reasoning,
        usage=data.get("usage") or {},
        model=data.get("model") or configured_model,
        wire_message=wire_assistant_message(content, calls, reasoning),
    )


def _tool_call(call_id: str, wire_name: str, arguments: str) -> ToolCall:
    """Decode one tool call. Arguments that are not a JSON object are reported on the call, not raised."""
    name = from_wire_name(wire_name)
    try:
        decoded = json.loads(arguments)
    except (TypeError, ValueError) as exc:
        return ToolCall(call_id, name, {}, str(exc))
    if not isinstance(decoded, dict):
        return ToolCall(call_id, name, {}, "arguments must be a JSON object")
    return ToolCall(call_id, name, decoded)


def _describe(error: Exception) -> str:
    """Exception type and message, e.g. "ReadTimeout: timed out". Some httpx errors carry no message."""
    message = str(error)
    return f"{type(error).__name__}: {message}" if message else type(error).__name__
