import json
import re

import pytest

from forwarder.agent.context import TicketContext, build_messages
from forwarder.agent.loop import AgentRunner
from forwarder.config import load_settings
from forwarder.llm import factory
from forwarder.llm.factory import build_agent_model
from forwarder.llm.mock_demo import DemoMockModel
from forwarder.llm.openai_compat import OpenAICompatModel
from forwarder.tools.logistics import P1_TOOLS, register_logistics_tools
from forwarder.tools.registry import ToolRegistry
from forwarder.world.ledger import Ledger

# A specific date, which no demo reply may contain: 11월 17일, 11月17日, 2026-11-17.
DATE = re.compile(r"\d{1,2}\s*[월月]\s*\d{1,2}\s*[일日]|\d{4}-\d{2}-\d{2}")


def user(msg, transcript="无"):
    return [{"role": "system", "content": "s"}, {"role": "user", "content": f"【工单】T-1\n【此前对话】\n{transcript}\n【客户最新消息】\n{msg}"}]


def tool_round(name, observation, arguments=None, call_id="call_1"):
    """An assistant message calling the tool, in wire form ("__" name, JSON-string arguments), and its answer."""
    call = {"id": call_id, "type": "function", "function": {"name": name.replace(".", "__"), "arguments": json.dumps(arguments or {}, ensure_ascii=False)}}
    return [
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call_id, "content": json.dumps(observation, ensure_ascii=False)},
    ]


def ticket(latest, transcript=(), customer=("C-CN-2207", "云帆优品")):
    """The two opening messages exactly as the agent loop builds them, with the full ticket header."""
    ctx = TicketContext("T-7", "web", customer[0], customer[1], list(transcript), latest)
    return build_messages(ctx, "货代")


# The brief's tests


def test_customs_question_calls_progress():
    r = DemoMockModel().complete(user("FWQD2611043 반입신고에서 멈췄어요"), tools=[])
    assert r.tool_calls[0].name == "customs.get_progress" and r.tool_calls[0].arguments == {"hbl": "FWQD2611043"}

def test_pccc_uses_hbl_from_transcript():
    r = DemoMockModel().complete(user("P230518447201", transcript="客户：FWQD2611043 통관 문의"), tools=[])
    assert r.tool_calls[0].name == "customs.submit_pccc" and r.tool_calls[0].arguments["hbl"] == "FWQD2611043"

def test_final_reply_after_pccc_hold_is_korean_and_asks_for_number():
    msgs = user("FWQD2611043 반입신고에서 멈췄어요") + tool_round("customs.get_progress", {"ok": True, "data": {"hold": {"code": "pccc_mismatch", "status": "open"}}})
    r = DemoMockModel().complete(msgs, tools=[])
    assert r.tool_calls == [] and "개인통관고유부호" in r.content
    assert not re.search(r"\d{1,2}월 \d{1,2}일", r.content)

def test_fallback_is_handoff():
    assert DemoMockModel().complete(user("你们公司地址在哪"), tools=[]).tool_calls[0].name == "ticket.handoff"

def test_factory_supports_explicit_test_provider(settings):
    assert isinstance(build_agent_model(settings), DemoMockModel)


# Choosing the first call


@pytest.mark.parametrize(
    ("messages", "name", "arguments"),
    [
        # Rule 1 comes first, and the H B/L in the message itself beats the one in the transcript.
        (ticket("FWQD2611150 的号码是 P230518447201", [("customer", "FWQD2611043 清关")]), "customs.submit_pccc", {"hbl": "FWQD2611150", "pccc": "P230518447201"}),
        # Otherwise the H B/L mentioned last in the transcript, whoever mentioned it.
        (ticket("P230518447201", [("customer", "FWQD2611043 清关"), ("agent", "FWQD2611150 已查到")]), "customs.submit_pccc", {"hbl": "FWQD2611150", "pccc": "P230518447201"}),
        (ticket("P230518447201"), "ticket.handoff", {"reason": "需要人工处理"}),  # no H B/L anywhere
        (ticket("FWQD2611150 还卡着吗"), "customs.get_progress", {"hbl": "FWQD2611150"}),
        (ticket("FWICN2611088 세관 통과했나요?"), "customs.get_progress", {"hbl": "FWICN2611088"}),
        (ticket("FWQD2611127 有破损吗，要重新包装"), "shipment.get", {"hbl": "FWQD2611127"}),  # an H B/L beats rules 4 and 5
        (ticket("H B/L 是 FWICN2611088"), "shipment.get", {"hbl": "FWICN2611088"}),
        # Rules 2 and 3 take the H B/L from the latest message only.
        (ticket("海关那边现在到哪了", [("customer", "FWICN2611088 查一下")]), "ticket.handoff", {"reason": "需要人工处理"}),
        (ticket("这批货请帮我 Repack 一下"), "shipment.list", {"customer_id": "C-CN-2207"}),  # in any case
        (ticket("검수 결과 파손이 있나요?", customer=("C-KR-1088", "한빛코스메틱")), "shipment.list", {"customer_id": "C-KR-1088"}),
        (user("请帮我重新包装"), "ticket.handoff", {"reason": "需要人工处理"}),  # no customer id in the header
        (user("验货有问题吗"), "ticket.handoff", {"reason": "需要人工处理"}),
    ],
    ids=["pccc_hbl_in_message", "pccc_hbl_in_transcript", "pccc_no_hbl", "customs_zh", "customs_ko", "hbl_beats_warehouse",
         "shipment", "hbl_not_from_transcript", "repack", "damage", "repack_no_customer", "damage_no_customer"],
)
def test_first_call_follows_the_first_rule_that_matches(messages, name, arguments):
    r = DemoMockModel().complete(messages, tools=[])
    assert [(call.name, call.arguments) for call in r.tool_calls] == [(name, arguments)]


def test_replies_are_built_in_the_wire_format_of_the_real_client():
    r = DemoMockModel().complete(user("FWQD2611043 통관"), tools=[])
    assert r.wire_message == {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "customs__get_progress", "arguments": '{"hbl": "FWQD2611043"}'}}],
    }
    assert (r.content, r.reasoning_content, r.usage, r.model) == ("", None, {}, "mock-demo")
    done = user("你们公司地址在哪") + tool_round("ticket.handoff", {"ok": True, "data": {"reason": "需要人工处理"}, "note": "该操作将在员工确认回复后生效"})
    final = DemoMockModel().complete(done, tools=[])
    assert final.tool_calls == [] and final.wire_message == {"role": "assistant", "content": final.content}
    assert "专人" in final.content and DemoMockModel.name == "mock-demo"


# Every reply template, in Chinese and in Korean


def ok(data, write=False):
    observation = {"ok": True, "data": data}
    return {**observation, "note": "该操作将在员工确认回复后生效"} if write else observation


LISTED = ("shipment.list", ok({"shipments": [{"hbl": "FWQD2611043", "warehouse_job": None}, {"hbl": "FWQD2611127", "warehouse_job": "WJ-1"}]}))
JOB = ("warehouse.get_job", ok({"job_id": "WJ-1", "steps": [{"key": "inspection", "status": "done", "result": "damaged", "cartons": ["C-1", "C-2"]}]}))
JOB_UNDAMAGED = ("warehouse.get_job", ok({"job_id": "WJ-1", "steps": [{"key": "inspection", "status": "pending"}]}))
CUSTOMS = {"zh": "FWQD2611043 清关", "ko": "FWQD2611043 통관"}
PCCC_GIVEN = {"zh": "FWQD2611043 的号码是 P230518447201", "ko": "FWQD2611043 번호는 P230518447201 입니다"}
WHERE = {"zh": "FWQD2611043 到哪了", "ko": "FWQD2611043 어디예요?"}
DAMAGE = {"zh": "有破损吗", "ko": "파손 있나요?"}
REPACK = {"zh": "请重新包装", "ko": "재포장 해 주세요"}


@pytest.mark.parametrize(
    ("messages", "rounds", "snippets"),
    [
        (CUSTOMS, [("customs.get_progress", ok({"hold": {"code": "pccc_mismatch", "status": "open"}}))], ("个人通关号码（P 加 12 位数字）", "개인통관고유부호(P와 숫자 12자리)")),
        (CUSTOMS, [("customs.get_progress", ok({"hold": {"code": "inspection", "status": "open"}}))], ("无需", "따로 하실 일은 없으며")),
        (CUSTOMS, [("customs.get_progress", ok({"hold": {"code": "quarantine", "status": "open", "reason_zh": "检疫中。", "reason_ko": "검역 중입니다."}}))], ("检疫中。我们正在跟进", "검역 중입니다. 담당자가")),
        (CUSTOMS, [("customs.get_progress", ok({"hold": {"code": "pccc_mismatch", "status": "resubmitted"}}))], ("等待重新申报的结果", "재신고 결과")),
        (CUSTOMS, [("customs.get_progress", ok({"hold": None, "steps": [{"status": "done", "label_zh": "入港申报", "label_ko": "입항보고"}, {"status": "pending", "label_zh": "进口申报", "label_ko": "수입신고"}]}))], ("「入港申报」", "'입항보고' 단계까지")),
        (CUSTOMS, [("customs.get_progress", ok({"hold": None, "steps": []}))], ("目前没有暂扣。", "현재 통관 보류는 없습니다.")),
        (PCCC_GIVEN, [("customs.submit_pccc", ok({"hbl": "FWQD2611043", "pccc_masked": "P2305****7201"}, write=True))], ("（P2305****7201）", "(P2305****7201)")),
        (WHERE, [("shipment.get", ok({"events": [{"label_zh": "青岛港离港", "label_ko": "칭다오항 출항"}, {"label_zh": "抵达仁川港", "label_ko": "인천항 도착"}]}))], ("「抵达仁川港」", "'인천항 도착'")),
        (WHERE, [("shipment.get", ok({"events": []}))], ("还没有运输动态", "아직 등록되지 않았습니다")),
        (DAMAGE, [LISTED, JOB], ("2 箱破损（箱号 C-1、C-2）", "박스 2개(C-1, C-2)")),
        (DAMAGE, [LISTED, JOB_UNDAMAGED], ("WJ-1 目前没有发现破损", "WJ-1에서는 현재까지 파손된 박스가")),
        (REPACK, [LISTED, JOB, ("warehouse.request_action", ok({"job_id": "WJ-1", "action": "repack", "cartons": ["C-1", "C-2"], "note": None}, write=True))], ("破损箱 C-1、C-2 重新包装", "파손 박스(C-1, C-2)를 재포장")),
        (REPACK, [LISTED, JOB_UNDAMAGED, ("warehouse.request_action", ok({"job_id": "WJ-1", "action": "repack", "cartons": [], "note": None}, write=True))], ("WJ-1 的货物重新包装", "WJ-1의 화물을 재포장")),
    ],
    ids=["pccc_hold", "inspection", "other_hold", "resubmitted", "no_hold", "no_hold_no_steps", "submitted",
         "shipment", "shipment_no_events", "damaged", "not_damaged", "repacked", "repacked_whole_job"],
)
def test_every_reply_template_in_both_languages(messages, rounds, snippets):
    for (lang, message), snippet in zip(messages.items(), snippets):
        history = ticket(message)
        for number, (name, observation) in enumerate(rounds, start=1):
            history += tool_round(name, observation, call_id=f"call_{number}")
        r = DemoMockModel().complete(history, tools=[])
        assert r.tool_calls == [] and snippet in r.content, (lang, r.content)
        assert bool(re.search("[가-힣]", r.content)) == (lang == "ko") and not DATE.search(r.content)


def test_a_failed_tool_call_gets_an_apology_in_the_customers_language():
    failed = tool_round("shipment.get", {"ok": False, "error": "not_found", "message": "没有找到这个单号的货物"}, {"hbl": "FWQD2611043"})
    zh = DemoMockModel().complete(user("FWQD2611043 到哪了") + failed, tools=[])
    ko = DemoMockModel().complete(user("FWQD2611043 어디쯤 왔나요?") + failed, tools=[])
    assert zh.tool_calls == [] and zh.content == "抱歉，暂时没有查到相关信息，已请同事核实。"
    assert ko.tool_calls == [] and ko.content.startswith("죄송합니다")


@pytest.mark.parametrize(
    ("transcript", "latest", "korean"),
    [
        # A message with neither Hangul nor Han characters follows the customer's earlier lines.
        ("客户：FWQD2611043 반입신고에서 멈췄어요", "P230518447201", True),
        ("客户：FWQD2611043 清关卡住了", "P230518447201", False),
        ("客户：FWQD2611043", "P230518447201", False),  # no language there either: Chinese
        ("客户：FWQD2611043 통관 문의\n客服：您好\n客户：清关卡住了", "P230518447201", True),  # any customer line with Hangul
        ("客户：FWQD2611043 清关卡住了\n客服：안녕하세요", "P230518447201", False),  # the agent's lines do not count
        ("客户：FWQD2611043\n통관 문의드려요", "P230518447201", True),  # a customer message's later lines do
        # Otherwise the latest message decides.
        ("客户：FWQD2611043 清关卡住了", "FWQD2611043 번호는 P230518447201 입니다", True),
        ("客户：FWQD2611043 반입신고에서 멈췄어요", "号码是 P230518447201", False),
    ],
    ids=["number_after_korean", "number_after_chinese", "number_after_neither", "any_customer_line",
         "agent_lines_ignored", "continuation_lines", "korean_latest_wins", "chinese_latest_wins"],
)
def test_reply_language_is_the_latest_messages_or_else_the_customers_earlier_one(transcript, latest, korean):
    submitted = ok({"hbl": "FWQD2611043", "pccc_masked": "P2305****7201"}, write=True)
    r = DemoMockModel().complete(user(latest, transcript) + tool_round("customs.submit_pccc", submitted), tools=[])
    assert r.tool_calls == [] and "P2305****7201" in r.content
    assert bool(re.search("[가-힣]", r.content)) == korean, r.content


# Through the real agent loop: registry with the logistics tools, seed world, ledger


@pytest.fixture
def ledger(db):
    return Ledger(db)


@pytest.fixture
def run_mock(ledger, world):
    """run_mock(customer_id, latest, transcript=(), run_id="R-1") -> RunResult of the mock on a fresh ticket."""
    registry = ToolRegistry()
    register_logistics_tools(registry)
    runner = AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=10)

    def run(customer_id, latest, transcript=(), run_id="R-1"):
        ctx = TicketContext("T-1", "web", customer_id, world.customers[customer_id]["name"], list(transcript), latest)
        return runner.run(run_id=run_id, ctx=ctx, model=DemoMockModel(), tool_names=P1_TOOLS, company="货代", emit=lambda step: None)

    return run


def tool_steps(result):
    return [step.data for step in result.steps if step.kind == "tool"]


def test_customs_hold_flow_asks_for_the_number_then_submits_it(run_mock, ledger):
    question = "FWQD2611043 반입신고에서 3일째 멈춰 있어요"
    first = run_mock("C-KR-0412", question, run_id="R-1")
    assert first.outcome == "final" and first.write_calls == []
    assert [(s["name"], s["arguments"]) for s in tool_steps(first)] == [("customs.get_progress", {"hbl": "FWQD2611043"})]
    assert "개인통관고유부호" in first.final_text and "수하인" in first.final_text and not DATE.search(first.final_text)

    transcript = [("customer", question), ("agent", first.final_text)]
    second = run_mock("C-KR-0412", "P230518447201", transcript, run_id="R-2")
    assert second.outcome == "final" and second.write_calls == ["customs.submit_pccc"]
    assert [(s["name"], s["arguments"]) for s in tool_steps(second)] == [("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": "P230518447201"})]
    assert "P2305****7201" in second.final_text and "P230518447201" not in second.final_text
    assert re.search("[가-힣]", second.final_text)  # Korean, like the customer's question before the bare number
    assert [record.tool for record in ledger.staged("R-2")] == ["customs.submit_pccc"]

    ledger.commit("R-2")  # staff confirmed the draft: the hold now shows the number as resubmitted
    third = run_mock("C-KR-0412", "FWQD2611043 통관 어떻게 됐어요?", run_id="R-3")
    assert third.outcome == "final" and [s["name"] for s in tool_steps(third)] == ["customs.get_progress"]
    assert "재신고" in third.final_text and "보내 주시면" not in third.final_text


def test_repack_flow_lists_finds_the_job_and_requests_the_repack(run_mock, ledger):
    r = run_mock("C-CN-2207", "验货发现破损的那几箱，请帮我重新包装")
    assert r.outcome == "final"
    assert [(s["name"], s["arguments"]) for s in tool_steps(r)] == [
        ("shipment.list", {"customer_id": "C-CN-2207"}),
        ("warehouse.get_job", {"job_id": "WJ-2611-031"}),
        ("warehouse.request_action", {"job_id": "WJ-2611-031", "action": "repack", "cartons": ["C-014", "C-027", "C-031"]}),
    ]
    assert [s["call_id"] for s in tool_steps(r)] == ["call_1", "call_2", "call_3"]  # unique within the run
    assert r.write_calls == ["warehouse.request_action"] and ledger.staged("R-1")[0].payload["action"] == "repack"
    assert all(carton in r.final_text for carton in ("C-014", "C-027", "C-031")) and "重新包装" in r.final_text


@pytest.mark.parametrize(
    ("customer_id", "message", "tools", "snippets"),
    [
        ("C-CN-3150", "我的包裹 FWQD2611150 在海关好几天了", ["customs.get_progress"], ["查验", "无需"]),
        ("C-CN-2207", "FWQD2611127 清关完成了吗", ["customs.get_progress"], ["没有暂扣", "监管仓出库申报"]),
        ("C-KR-1088", "FWICN2611088 지금 어디쯤 있나요?", ["shipment.get"], ["인천항 출항"]),
        ("C-CN-2207", "验货结果怎么样？有破损吗", ["shipment.list", "warehouse.get_job"], ["C-014", "C-027", "C-031", "重新包装"]),
        ("C-KR-0412", "재포장 부탁드려요", ["shipment.list", "ticket.handoff"], ["담당자"]),  # no warehouse job to repack
        ("C-KR-0412", "FWQD2611127 통관 어떻게 됐어요?", ["customs.get_progress"], ["죄송합니다"]),  # someone else's H B/L
        ("C-CN-3150", "你们公司地址在哪", ["ticket.handoff"], ["专人"]),
    ],
    ids=["inspection", "no_hold", "shipment", "damage", "repack_without_job", "tool_error", "handoff"],
)
def test_every_rule_ends_with_a_reply_through_the_agent_loop(run_mock, customer_id, message, tools, snippets):
    r = run_mock(customer_id, message)
    assert r.outcome == "final" and [s["name"] for s in tool_steps(r)] == tools
    assert all(snippet in r.final_text for snippet in snippets) and not DATE.search(r.final_text)


# The factory


def test_factory_builds_the_deepseek_client_from_settings(tmp_path, monkeypatch):
    settings = load_settings(
        env_file=tmp_path / "test.env",
        overrides={
            "LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": "sk-test", "DEEPSEEK_BASE_URL": "https://llm.example.test",
            "AGENT_MODEL": "deepseek-pro", "AGENT_THINKING": "true",
        },
    )
    model = build_agent_model(settings)
    assert isinstance(model, OpenAICompatModel) and model.name == "deepseek-pro"
    seen = {}
    monkeypatch.setattr(factory, "OpenAICompatModel", lambda **kwargs: seen.update(kwargs) or "client")
    assert build_agent_model(settings) == "client"
    assert seen == {"base_url": "https://llm.example.test", "api_key": "sk-test", "model": "deepseek-pro", "thinking": True}
