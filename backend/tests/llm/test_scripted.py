import httpx
import pytest

from forwarder.llm.base import LLMError, ModelReply
from forwarder.llm.openai_compat import OpenAICompatModel
from forwarder.llm.scripted import ScriptedModel, reply_text, reply_tool, reply_tools


def client_reply(message: dict) -> ModelReply:
    """What OpenAICompatModel returns when the API answers with this assistant message."""
    body = {"model": "deepseek-flash", "choices": [{"index": 0, "message": message, "finish_reason": "stop"}], "usage": {}}
    model = OpenAICompatModel(
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        model="deepseek-flash",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    return model.complete([], None)


def test_scripted_returns_in_order_and_records_calls():
    m = ScriptedModel([reply_tool("shipment.get", {"hbl": "X"}), reply_text("done")])
    assert m.complete([{"role": "user", "content": "a"}], None).tool_calls[0].name == "shipment.get"
    assert m.complete([{"role": "user", "content": "b"}], None).content == "done"
    assert len(m.calls) == 2


def test_scripted_raises_exception_items():
    error = LLMError(429, "rate limited")
    m = ScriptedModel([error])
    with pytest.raises(LLMError) as exc:
        m.complete([{"role": "user", "content": "a"}], None)
    assert exc.value is error and len(m.calls) == 1


def test_scripted_records_a_deep_copy_of_each_call():
    messages = [{"role": "user", "content": "a"}]
    m = ScriptedModel([reply_text("ok")])
    m.complete(messages, None)
    messages[0]["content"] = "changed"
    messages.append({"role": "assistant", "content": "ok"})
    assert m.calls == [[{"role": "user", "content": "a"}]]


def test_scripted_hands_out_a_fresh_copy_of_each_reply():
    reply = reply_tool("shipment.get", {"hbl": "X"})
    m = ScriptedModel([reply, reply])
    first = m.complete([], None)
    first.tool_calls[0].arguments_error = "changed"
    first.wire_message["content"] = "changed"
    second = m.complete([], None)
    assert second.tool_calls[0].arguments_error is None and second.wire_message["content"] == ""


def test_scripted_fails_loudly_when_out_of_replies():
    m = ScriptedModel([reply_text("only")])
    m.complete([], None)
    with pytest.raises(AssertionError, match="no scripted reply left"):
        m.complete([], None)


@pytest.mark.parametrize(
    ("helper_reply", "api_message"),
    [
        (reply_text("통관 보류 상태입니다."), {"role": "assistant", "content": "통관 보류 상태입니다."}),
        (
            reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}, reasoning="先查清关"),
            {
                "role": "assistant",
                "content": "",
                "reasoning_content": "先查清关",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "customs__get_progress", "arguments": '{"hbl": "FWQD2611043"}'},
                    }
                ],
            },
        ),
        (
            reply_tools([("shipment.get", {"hbl": "FWQD2611043"}), ("ticket.handoff", {"reason": "需要人工处理"})]),
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "shipment__get", "arguments": '{"hbl": "FWQD2611043"}'}},
                    {"id": "call_2", "type": "function", "function": {"name": "ticket__handoff", "arguments": '{"reason": "需要人工处理"}'}},
                ],
            },
        ),
    ],
    ids=["text", "tool_with_reasoning", "two_tools"],
)
def test_helpers_build_the_reply_the_client_would(helper_reply, api_message):
    client = client_reply(api_message)
    assert helper_reply.wire_message == client.wire_message
    assert helper_reply.tool_calls == client.tool_calls
    assert (helper_reply.content, helper_reply.reasoning_content) == (client.content, client.reasoning_content)
