import copy
import json

import httpx
import pytest

from forwarder.llm.base import LLMError, from_wire_name, to_wire_name
from forwarder.llm.openai_compat import OpenAICompatModel


class CapturedRequest:
    """The last request a model sent through the mock transport."""

    def __init__(self) -> None:
        self.request: httpx.Request | None = None

    @property
    def url(self) -> httpx.URL:
        return self.request.url

    @property
    def headers(self) -> httpx.Headers:
        return self.request.headers

    @property
    def content(self) -> bytes:
        return self.request.content

    def json(self) -> dict:
        return json.loads(self.request.content)


@pytest.fixture
def captured():
    return CapturedRequest()


def make_model(captured, *, thinking=False, status=200, response=None, base_url="https://api.deepseek.com"):
    """A DeepSeek client whose requests all get `response`: a JSON body, or raw text when it is a str."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.request = request
        if isinstance(response, str):
            return httpx.Response(status, text=response)
        return httpx.Response(status, json=response)

    return OpenAICompatModel(
        base_url=base_url,
        api_key="sk-test",
        model="deepseek-flash",
        thinking=thinking,
        transport=httpx.MockTransport(handler),
    )


def failing_model(error: Exception) -> OpenAICompatModel:
    """A DeepSeek client whose requests never get a response."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return OpenAICompatModel(
        base_url="https://api.deepseek.com", api_key="sk-test", model="deepseek-flash", transport=httpx.MockTransport(handler)
    )


def fn_tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "按提单号查询",
            "parameters": {"type": "object", "properties": {"hbl": {"type": "string"}}, "required": ["hbl"]},
        },
    }


def completion(message, finish_reason, usage=None):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1795000000,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage if usage is not None else {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
    }


def tool_call_response(wire_name, args_str):
    # content is null next to tool calls, as OpenAI-format APIs may send it.
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": wire_name, "arguments": args_str}}],
    }
    return completion(message, "tool_calls")


def text_response(text, reasoning=None, usage=None):
    message = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return completion(message, "stop", usage)


def test_payload_disables_thinking_and_maps_tool_names(captured):
    model = make_model(captured, response=tool_call_response("customs__get_progress", '{"hbl":"FWQD2611043"}'))
    reply = model.complete([{"role": "user", "content": "hi"}], tools=[fn_tool("customs.get_progress")])
    body = captured.json()
    assert captured.url == "https://api.deepseek.com/chat/completions"
    assert body["thinking"] == {"type": "disabled"}
    assert body["tools"][0]["function"]["name"] == "customs__get_progress"
    assert "parallel_tool_calls" not in body
    assert captured.headers["authorization"] == "Bearer sk-test"
    assert reply.tool_calls[0].name == "customs.get_progress"
    assert reply.tool_calls[0].arguments == {"hbl": "FWQD2611043"}
    assert reply.wire_message["tool_calls"][0]["function"]["name"] == "customs__get_progress"


def test_thinking_enabled_keeps_reasoning_content(captured):
    model = make_model(captured, thinking=True, response=text_response("好的", reasoning="先查清关"))
    reply = model.complete([{"role": "user", "content": "x"}], tools=None)
    assert captured.json()["thinking"] == {"type": "enabled"}
    assert reply.reasoning_content == "先查清关" and reply.wire_message["reasoning_content"] == "先查清关"


def test_thinking_none_omits_param(captured):
    make_model(captured, thinking=None, response=text_response("ok")).complete([{"role": "user", "content": "x"}], None)
    assert "thinking" not in captured.json()


def test_invalid_json_arguments_reported_not_raised(captured):
    reply = make_model(captured, response=tool_call_response("shipment__get", "{bad")).complete([], [fn_tool("shipment.get")])
    assert reply.tool_calls[0].arguments == {} and reply.tool_calls[0].arguments_error


def test_complete_raises_llm_error_on_http_error(captured):
    model = make_model(captured, status=401, response={"error": {"message": "Authentication Fails"}})
    with pytest.raises(LLMError) as exc:
        model.complete([{"role": "user", "content": "x"}], None)
    assert exc.value.status == 401 and "Authentication" in exc.value.body


def test_usage_is_returned(captured):
    reply = make_model(captured, response=text_response("ok", usage={"prompt_tokens": 12, "completion_tokens": 3})).complete([], None)
    assert reply.usage["prompt_tokens"] == 12


def test_wire_names_swap_dots_for_double_underscores():
    assert to_wire_name("customs.get_progress") == "customs__get_progress"
    assert from_wire_name("customs__get_progress") == "customs.get_progress"


def test_request_body_holds_only_documented_fields(captured):
    messages = [{"role": "system", "content": "你是物流客服"}, {"role": "user", "content": "반입신고에서 멈췄어요"}]
    tools = [fn_tool("customs.get_progress")]
    tools_before = copy.deepcopy(tools)
    make_model(captured, response=text_response("ok")).complete(messages, tools)
    body = captured.json()
    assert captured.request.method == "POST"
    assert set(body) == {"model", "messages", "tools", "tool_choice", "thinking"}
    assert body["model"] == "deepseek-flash" and body["messages"] == messages and body["tool_choice"] == "auto"
    # Only the function name changes, and the caller's specs are left as they were.
    assert body["tools"] == [{**tools[0], "function": {**tools[0]["function"], "name": "customs__get_progress"}}]
    assert tools == tools_before
    assert "반입신고에서 멈췄어요".encode() in captured.content  # UTF-8 text, not \u escapes
    assert captured.request.extensions["timeout"] == {"connect": 60.0, "read": 60.0, "write": 60.0, "pool": 60.0}


@pytest.mark.parametrize("tools", [None, []])
def test_no_tools_sends_neither_tools_nor_tool_choice(captured, tools):
    make_model(captured, response=text_response("ok")).complete([{"role": "user", "content": "x"}], tools)
    body = captured.json()
    assert "tools" not in body and "tool_choice" not in body


def test_text_reply_fields(captured):
    usage = {
        "prompt_tokens": 40,
        "completion_tokens": 8,
        "total_tokens": 48,
        "prompt_cache_hit_tokens": 32,
        "prompt_cache_miss_tokens": 8,
        "completion_tokens_details": {"reasoning_tokens": 0},
    }
    response = text_response("您好", usage=usage)
    response["model"] = "deepseek-flash-2611"
    reply = make_model(captured, response=response).complete([], None)
    assert reply.content == "您好" and reply.tool_calls == [] and reply.reasoning_content is None
    assert reply.wire_message == {"role": "assistant", "content": "您好"}
    assert reply.usage == usage
    assert reply.model == "deepseek-flash-2611"  # the model the API says answered


def test_wire_message_echoes_the_returned_tool_call(captured):
    reply = make_model(captured, response=tool_call_response("shipment__get", "{bad")).complete([], [fn_tool("shipment.get")])
    assert reply.content == "" and reply.tool_calls[0].id == "call_0"
    assert reply.wire_message == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_0", "type": "function", "function": {"name": "shipment__get", "arguments": "{bad"}}],
    }


def test_arguments_must_be_a_json_object(captured):
    reply = make_model(captured, response=tool_call_response("shipment__get", "[1, 2]")).complete([], [fn_tool("shipment.get")])
    assert reply.tool_calls[0].arguments == {} and reply.tool_calls[0].arguments_error


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")], ids=["connect", "timeout"]
)
def test_network_failures_raise_llm_error_without_status(error):
    with pytest.raises(LLMError) as exc:
        failing_model(error).complete([{"role": "user", "content": "x"}], None)
    assert exc.value.status is None
    assert str(exc.value).startswith("LLM network error: ") and str(error) in str(exc.value)


def test_server_error_raises_llm_error(captured):
    model = make_model(captured, status=503, response={"error": {"message": "Server overloaded"}})
    with pytest.raises(LLMError) as exc:
        model.complete([], None)
    assert exc.value.status == 503 and "Server overloaded" in exc.value.body


@pytest.mark.parametrize("response", ["<html>bad gateway</html>", {"choices": []}], ids=["not_json", "no_choices"])
def test_unusable_success_body_raises_llm_error(captured, response):
    with pytest.raises(LLMError) as exc:
        make_model(captured, response=response).complete([], None)
    assert exc.value.status == 200


def test_llm_error_text():
    error = LLMError(429, "rate limited")
    assert (error.status, error.body) == (429, "rate limited") and isinstance(error, Exception)
    assert str(error) == "LLM HTTP 429: rate limited"
    assert str(LLMError(None, "ReadTimeout: timed out")) == "LLM network error: ReadTimeout: timed out"


def test_trailing_slash_in_base_url_is_ignored(captured):
    make_model(captured, base_url="https://api.deepseek.com/", response=text_response("ok")).complete([], None)
    assert captured.url == "https://api.deepseek.com/chat/completions"
