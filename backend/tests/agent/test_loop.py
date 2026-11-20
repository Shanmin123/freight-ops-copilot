import json

import pytest

from forwarder.agent.loop import AgentRunner
from forwarder.llm.base import LLMError
from forwarder.llm.scripted import ScriptedModel, reply_text, reply_tool, reply_tools
from forwarder.tools.logistics import P1_TOOLS

PCCC = "P230518447201"


class ToolsRecordingModel(ScriptedModel):
    """ScriptedModel that also keeps the tools argument of every call."""

    def __init__(self, replies):
        super().__init__(replies)
        self.tools = []

    def complete(self, messages, tools):
        self.tools.append(tools)
        return super().complete(messages, tools)


def test_tool_then_final(runner, ctx_kr_ticket):
    model = ScriptedModel([reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}), reply_text("통관 보류 상태입니다.")])
    steps = []
    r = runner.run(run_id="R-1", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="货代", emit=steps.append)
    assert r.outcome == "final" and r.final_text == "통관 보류 상태입니다." and r.write_calls == []
    assert [s.kind for s in steps] == ["model", "tool", "model"]
    tool_msg = model.calls[1][-1]
    supplied = json.loads(tool_msg["content"])
    assert tool_msg["role"] == "tool" and supplied["ok"] is True
    assert supplied["data"]["steps"][-1]["key"] == "bonded_in"
    assert all(step["status"] == "done" and "at" not in step for step in supplied["data"]["steps"])
    assert supplied["data"]["hold"]["code"] == "pccc_mismatch" and "since" not in supplied["data"]["hold"]
    recorded = steps[1].data["observation"]["data"]
    assert any(step["status"] == "pending" for step in recorded["steps"])
    assert recorded["steps"][0]["at"] and recorded["hold"]["since"]


def test_write_call_recorded_and_staged(runner, ctx_kr_ticket, ledger):
    model = ScriptedModel([reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": "P230518447201"}), reply_text("제출했습니다.")])
    r = runner.run(run_id="R-2", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert r.write_calls == ["customs.submit_pccc"] and len(ledger.staged("R-2")) == 1


def test_multiple_tool_calls_only_first_executed(runner, ctx_kr_ticket):
    model = ScriptedModel([reply_tools([("shipment.get", {"hbl": "FWQD2611043"}), ("customs.get_progress", {"hbl": "FWQD2611043"})]), reply_text("ok")])
    runner.run(run_id="R-3", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    tool_msgs = [m for m in model.calls[1] if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert json.loads(tool_msgs[1]["content"])["error"] == "only_one_tool_per_step"


def test_invalid_json_arguments_become_tool_error(runner, ctx_kr_ticket):
    bad = reply_tool("shipment.get", {}); bad.tool_calls[0].arguments_error = "Expecting value"
    model = ScriptedModel([bad, reply_text("ok")])
    r = runner.run(run_id="R-4", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert json.loads(model.calls[1][-1]["content"])["error"] == "invalid_json_arguments" and r.outcome == "final"


def test_reasoning_content_passed_back(runner, ctx_kr_ticket):
    model = ScriptedModel([reply_tool("shipment.get", {"hbl": "FWQD2611043"}, reasoning="先查货物"), reply_text("ok")])
    runner.run(run_id="R-5", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assistant = [m for m in model.calls[1] if m["role"] == "assistant"][0]
    assert assistant["reasoning_content"] == "先查货物"


def test_max_steps(runner_max2, ctx_kr_ticket):
    model = ScriptedModel([reply_tool("shipment.get", {"hbl": "FWQD2611043"})] * 3)
    r = runner_max2.run(run_id="R-6", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert r.outcome == "max_steps" and r.final_text is None


def test_llm_error_outcome(runner, ctx_kr_ticket):
    model = ScriptedModel([LLMError(429, "rate limited")])
    r = runner.run(run_id="R-7", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert r.outcome == "error" and "429" in r.error


def test_usage_is_summed(runner, ctx_kr_ticket):
    a = reply_tool("shipment.get", {"hbl": "FWQD2611043"}); a.usage = {"prompt_tokens": 10, "completion_tokens": 2}
    b = reply_text("ok"); b.usage = {"prompt_tokens": 15, "completion_tokens": 4}
    r = runner.run(run_id="R-8", ctx=ctx_kr_ticket, model=ScriptedModel([a, b]), tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert r.usage == {"prompt_tokens": 25, "completion_tokens": 6}


def test_steps_carry_the_documented_data(runner, ctx_kr_ticket):
    a = reply_tool("shipment.get", {"hbl": "FWQD2611043"}); a.usage = {"prompt_tokens": 7}
    model = ScriptedModel([a, reply_text("끝")])
    steps = []
    r = runner.run(run_id="R-10", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=steps.append)
    assert r.steps == steps and [s.index for s in steps] == [0, 1, 2]
    assert steps[0].data == {
        "content": "",
        "tool_calls": [{"id": "call_1", "name": "shipment.get", "arguments": {"hbl": "FWQD2611043"}}],
        "usage": {"prompt_tokens": 7},
    }
    tool = steps[1].data
    assert set(tool) == {"call_id", "name", "arguments", "kind", "observation"}
    assert (tool["call_id"], tool["name"], tool["arguments"], tool["kind"]) == ("call_1", "shipment.get", {"hbl": "FWQD2611043"}, "read")
    assert tool["observation"]["ok"] is True and tool["observation"]["data"]["hbl"] == "FWQD2611043"
    # The model saw exactly the recorded observation, with Chinese and Korean text left unescaped.
    assert model.calls[1][-1] == {"role": "tool", "tool_call_id": "call_1", "content": json.dumps(tool["observation"], ensure_ascii=False)}
    assert steps[2].data == {"content": "끝", "tool_calls": [], "usage": {}}
    assert r.model == "scripted" and r.error is None


def test_every_call_is_answered_in_order_with_its_tool_kind(runner, ctx_kr_ticket, ledger):
    reply = reply_tools([
        ("shipment.get", {"hbl": "FWQD2611043"}),
        ("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": PCCC}),
        ("ticket.handoff", {}),
        ("no.such_tool", {}),
    ])
    reply.tool_calls[2].arguments_error = "Expecting value"
    model = ScriptedModel([reply, reply_text("ok")])
    steps = []
    r = runner.run(run_id="R-11", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=steps.append)
    answers = [(s.data["call_id"], s.data["kind"], s.data["observation"].get("error")) for s in steps if s.kind == "tool"]
    assert answers == [
        ("call_1", "read", None),
        ("call_2", "write", "only_one_tool_per_step"),
        ("call_3", "write", "invalid_json_arguments"),
        ("call_4", "read", "only_one_tool_per_step"),
    ]
    assert [m["tool_call_id"] for m in model.calls[1] if m["role"] == "tool"] == ["call_1", "call_2", "call_3", "call_4"]
    # Refused calls never run, so nothing is staged; they still count as write calls of the run.
    assert ledger.staged("R-11") == []
    assert r.write_calls == ["customs.submit_pccc", "ticket.handoff"]


def test_max_steps_still_runs_the_tool_calls_of_the_last_model_call(runner_max2, ctx_kr_ticket, ledger):
    model = ScriptedModel([
        reply_tool("shipment.get", {"hbl": "FWQD2611043"}),
        reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": PCCC}, call_id="call_2"),
        reply_text("never requested"),
    ])
    steps = []
    r = runner_max2.run(run_id="R-12", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=steps.append)
    assert len(model.calls) == 2 and [s.kind for s in steps] == ["model", "tool", "model", "tool"]
    assert r.outcome == "max_steps" and r.error is None
    assert r.write_calls == ["customs.submit_pccc"] and len(ledger.staged("R-12")) == 1


def test_run_sees_its_own_staged_writes_and_no_one_elses(runner, ctx_kr_ticket):
    model = ScriptedModel([
        reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": PCCC}),
        reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}, call_id="call_2"),
        reply_text("ok"),
    ])
    steps = []
    runner.run(run_id="R-13", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=steps.append)
    assert steps[-2].data["observation"]["data"]["hold"]["status"] == "resubmitted"

    other = ScriptedModel([reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}), reply_text("ok")])
    steps = []
    runner.run(run_id="R-14", ctx=ctx_kr_ticket, model=other, tool_names=P1_TOOLS, company="G", emit=steps.append)
    assert steps[1].data["observation"]["data"]["hold"]["status"] == "open"


def test_only_the_profile_tools_are_offered_and_allowed(runner, ctx_kr_ticket):
    model = ToolsRecordingModel([reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}), reply_text("ok")])
    steps = []
    profile = ["shipment.get", "ghost.lookup", "shipment.list"]  # ghost.lookup is not registered
    runner.run(run_id="R-15", ctx=ctx_kr_ticket, model=model, tool_names=profile, company="G", emit=steps.append)
    assert [t["function"]["name"] for t in model.tools[0]] == ["shipment.get", "shipment.list"]
    assert steps[1].data["observation"]["error"] == "tool_not_allowed"


def test_final_text_is_stripped_and_a_blank_reply_is_an_error(runner, ctx_kr_ticket):
    r = runner.run(run_id="R-16", ctx=ctx_kr_ticket, model=ScriptedModel([reply_text("  확인했습니다.\n")]), tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert (r.outcome, r.final_text, r.error) == ("final", "확인했습니다.", None)
    r = runner.run(run_id="R-17", ctx=ctx_kr_ticket, model=ScriptedModel([reply_text(" \n ")]), tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert (r.outcome, r.final_text, r.error) == ("error", None, "empty_reply")


def test_llm_error_mid_run_keeps_what_happened_so_far(runner, ctx_kr_ticket):
    a = reply_tool("shipment.get", {"hbl": "FWQD2611043"}); a.usage = {"prompt_tokens": 10}
    steps = []
    model = ScriptedModel([a, LLMError(None, "ReadTimeout: timed out")])
    r = runner.run(run_id="R-18", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=steps.append)
    assert (r.outcome, r.final_text, r.error) == ("error", None, "LLM network error: ReadTimeout: timed out")
    assert r.steps == steps and [s.kind for s in steps] == ["model", "tool"] and r.usage == {"prompt_tokens": 10}


def test_other_model_failures_propagate(runner, ctx_kr_ticket):
    with pytest.raises(RuntimeError, match="boom"):
        runner.run(run_id="R-19", ctx=ctx_kr_ticket, model=ScriptedModel([RuntimeError("boom")]), tool_names=P1_TOOLS, company="G", emit=lambda s: None)


def test_usage_sums_only_top_level_numbers(runner, ctx_kr_ticket):
    a = reply_tool("shipment.get", {"hbl": "FWQD2611043"})
    a.usage = {"prompt_tokens": 10, "cost": 0.5, "completion_tokens_details": {"reasoning_tokens": 3}}
    b = reply_text("ok")
    b.usage = {"prompt_tokens": 5, "cost": 0.25, "completion_tokens_details": {"reasoning_tokens": 4}, "cached": True, "tier": "x"}
    r = runner.run(run_id="R-20", ctx=ctx_kr_ticket, model=ScriptedModel([a, b]), tool_names=P1_TOOLS, company="G", emit=lambda s: None)
    assert r.usage == {"prompt_tokens": 15, "cost": 0.75}


def test_changing_emitted_steps_does_not_change_the_run(runner, ctx_kr_ticket, ledger):
    def mask_in_place(step):  # e.g. a consumer hiding the PCCC before storing the step
        for call in step.data.get("tool_calls", []):
            call["arguments"]["pccc"] = "P2305****7201"

    model = ScriptedModel([reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": PCCC}), reply_text("ok")])
    runner.run(run_id="R-21", ctx=ctx_kr_ticket, model=model, tool_names=P1_TOOLS, company="G", emit=mask_in_place)
    assert json.loads(model.calls[1][-1]["content"])["ok"] is True and len(ledger.staged("R-21")) == 1


def test_max_steps_must_be_positive(registry, ledger, world):
    with pytest.raises(ValueError, match="max_steps"):
        AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=0)
