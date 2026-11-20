import json
import re

import pytest

from forwarder.llm.base import LLMError
from forwarder.llm.scripted import reply_text, reply_tool
from forwarder.tickets.executor import InlineExecutor
from forwarder.tickets.service import NotADraftError
from forwarder.world.ledger import Ledger

PCCC = "P230518447201"
ACK_ZH = "已收到您的消息，我们正在核对，稍后回复您。"
ACK_KO = "메시지를 확인했습니다. 확인 후 곧 답변드리겠습니다."


def submit_pccc():
    return reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": PCCC})


def test_readonly_run_autosends(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("customs.get_progress", {"hbl": "FWQD2611043"}), reply_text("통관 보류 중입니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="FWQD2611043 반입신고에서 멈췄어요")
    msgs = repo.messages(t.id)
    assert [m.role for m in msgs] == ["customer", "agent"] and msgs[1].status == "sent"
    assert repo.get_ticket(t.id).status == "ai_replied"
    assert "run.step" in [e.type for e in bus.since(0)]


def test_write_run_creates_draft_with_ack_and_staged(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": "P230518447201"}), reply_text("정정 번호를 제출했습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="P230518447201 입니다")
    msgs = repo.messages(t.id)
    assert [(m.kind, m.status) for m in msgs if m.role == "agent"] == [("ack", "sent"), ("normal", "draft")]
    assert repo.get_ticket(t.id).status == "awaiting_confirmation"
    draft = repo.pending_draft(t.id)
    assert len(ledger.staged(draft.run_id)) == 1 and ledger.effects() == []


def test_confirm_commits_and_sends(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("customs.submit_pccc", {"hbl": "FWQD2611043", "pccc": "P230518447201"}), reply_text("제출했습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="P230518447201")
    d = repo.pending_draft(t.id)
    sent = svc.confirm_draft(t.id, d.id, text="정정된 번호를 관세사에 전달했습니다.")
    assert sent.status == "sent" and sent.edited and sent.text.startswith("정정된")
    assert len(ledger.effects()) == 1 and repo.get_ticket(t.id).status == "replied"


def test_regenerate_discards_and_reruns(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "x"}), reply_text("转人工"), reply_text("新草稿")], auto_reply=False)
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="帮我转人工")
    d = repo.pending_draft(t.id)
    svc.regenerate_draft(t.id, d.id)
    assert repo.messages(t.id)[-2].status == "discarded" and ledger.staged(d.run_id) == []
    assert repo.pending_draft(t.id).text == "新草稿"


def test_new_message_discards_pending_draft(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "x"}), reply_text("草稿1"), reply_text("草稿2")], auto_reply=False)
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第一条")
    first = repo.pending_draft(t.id)
    svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第二条")
    assert repo.pending_draft(t.id).text == "草稿2" and ledger.staged(first.run_id) == []
    assert sum(1 for m in repo.messages(t.id) if m.kind == "ack") == 2


def test_message_during_run_discards_result_and_reruns(svc_factory_manual):
    svc, repo, ex, model = svc_factory_manual()
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="첫 번째")
    model.on_call(0, lambda: svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="두 번째"))
    ex.run_next()  # 第一次运行，运行中收到第二条
    assert [m.text for m in repo.messages(t.id) if m.role == "agent"] == []
    ex.run_next()  # 重跑
    agent = [m for m in repo.messages(t.id) if m.role == "agent"]
    assert len(agent) == 1 and agent[0].status == "sent"


def test_llm_error_marks_ticket_needs_human(svc_factory):
    svc, repo, bus, ledger = svc_factory([LLMError(401, "Authentication Fails")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="안녕하세요")
    assert repo.get_ticket(t.id).status == "needs_human"
    assert [m.role for m in repo.messages(t.id)] == ["customer"]
    fin = [e for e in bus.since(0) if e.type == "run.finished"][-1]
    assert "401" in fin.payload["error"]


def test_auto_reply_off_drafts_readonly(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_text("你好")], auto_reply=False)
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="在吗")
    assert repo.pending_draft(t.id) is not None


@pytest.mark.parametrize(
    ("customer_id", "message", "expected_ack"),
    [
        ("C-KR-0412", "我想咨询清关进度", ACK_ZH),
        ("C-CN-3150", "통관 진행이 궁금합니다", ACK_KO),
    ],
)
def test_ack_follows_message_language_including_number_only_followup(svc_factory, customer_id, message, expected_ack):
    svc, repo, _, _ = svc_factory([reply_text("草稿"), reply_text("草稿")], auto_reply=False)
    ticket, _ = svc.customer_message(customer_id=customer_id, channel="web", text=message)
    svc.customer_message(customer_id=customer_id, channel="web", text=PCCC)

    assert [item.text for item in repo.messages(ticket.id) if item.kind == "ack"] == [expected_ack, expected_ack]


def test_transcript_excludes_ack_and_drafts(svc_factory):
    svc, repo, bus, ledger, model = svc_factory([reply_tool("ticket.handoff", {"reason": "x"}), reply_text("草稿"), reply_text("ok")], auto_reply=False, return_model=True)
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第一条")
    svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第二条")
    user_block = model.calls[-1][1]["content"]
    assert "客户：第一条" in user_block and "已收到您的消息" not in user_block and "草稿" not in user_block


# Beyond the brief's tests: events, the rulings on privacy (R17/R23), handoff (R18), dropped writes
# (R21) and crashed runs (R24), reruns, the demo reset, and what may be confirmed or rewritten.


def test_an_auto_sent_run_publishes_each_change_in_order(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_text("통관 보류 중입니다.")])
    t, asked = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="FWQD2611043 통관 문의")
    events = bus.since(0)
    assert [e.type for e in events] == [
        "message.created", "ticket.updated",  # the customer message; the ticket back to processing
        "run.started", "ticket.typing", "run.step",
        "message.created", "ticket.updated", "ticket.typing", "run.finished",
    ]
    assert {e.ticket_id for e in events} == {t.id}
    [run] = repo.runs(t.id)
    reply = repo.messages(t.id)[-1]
    step = {"index": 0, "kind": "model", "data": {"content": "통관 보류 중입니다.", "tool_calls": [], "usage": {}}}
    assert re.fullmatch(r"R-[0-9a-f]{12}", run.id) and reply.run_id == run.id
    assert events[0].payload == {"message": asked.to_dict()}
    assert events[2].payload == {"run_id": run.id, "model": "scripted"}
    assert [e.payload for e in events if e.type == "ticket.typing"] == [{"typing": True}, {"typing": False}]
    assert events[4].payload == {"run_id": run.id, "step": step} and run.steps == [step]
    assert events[5].payload == {"message": reply.to_dict()}
    assert events[6].payload == {"ticket": repo.get_ticket(t.id).to_dict()}
    assert events[8].payload == {"run_id": run.id, "outcome": "final", "decision": "auto_sent", "error": None, "usage": {}}
    assert (run.status, run.outcome, run.decision, run.model) == ("finished", "final", "auto_sent", "scripted")


def test_messages_go_to_the_customers_latest_ticket_on_that_channel(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_text("네"), reply_text("네"), reply_text("네")])
    text = "FWQD2611043 반입신고에서 멈췄어요. 언제쯤 통관이 될까요? 확인 부탁드립니다."
    first, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=text)
    again, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="확인됐나요?")
    web, _ = svc.customer_message(customer_id="C-KR-0412", channel="web", text="웹에서 문의합니다")
    assert first.subject == text[:30] and again.id == first.id and web.id != first.id
    assert [m.text for m in repo.messages(first.id) if m.role == "customer"] == [text, "확인됐나요?"]
    with pytest.raises(KeyError):
        svc.customer_message(customer_id="C-XX-0000", channel="web", text="hi")
    assert len(repo.list_tickets()) == 2


def test_a_new_tickets_subject_has_the_pccc_masked(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_text("확인했습니다."), reply_text("확인했습니다.")])
    t, asked = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=f"{PCCC} 번호로 정정해 주세요")
    assert t.subject == repo.get_ticket(t.id).subject == "P2305****7201 번호로 정정해 주세요"
    assert asked.text == f"{PCCC} 번호로 정정해 주세요"  # the message itself stays as written
    # Masked before the cut to 30 characters, so a number across the cut shows no more digits either.
    web, _ = svc.customer_message(customer_id="C-KR-0412", channel="web", text=f"정정된 개인통관고유부호를 보내드립니다: {PCCC}")
    assert web.subject == "정정된 개인통관고유부호를 보내드립니다: P2305***"
    subjects = {e.payload["ticket"]["subject"] for e in bus.since(0) if e.type == "ticket.updated"}
    assert subjects == {"P2305****7201 번호로 정정해 주세요", "정정된 개인통관고유부호를 보내드립니다: P2305***"}


def test_run_steps_are_stored_and_published_with_the_pccc_masked(svc_factory):
    svc, repo, bus, ledger = svc_factory([submit_pccc(), reply_text("제출했습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=f"{PCCC} 입니다")
    [run] = repo.runs(t.id)
    assert len(ledger.staged(run.id)) == 1  # the tool itself got the full number
    stored = json.dumps(run.steps, ensure_ascii=False)
    published = json.dumps([e.payload for e in bus.since(0) if e.type == "run.step"], ensure_ascii=False)
    for text in (stored, published):
        # The model's call arguments, the tool step's arguments, and the staged payload it returned.
        assert PCCC not in text and text.count("P2305****7201") == 3


def test_agent_replies_are_stored_and_sent_with_the_pccc_masked(svc_factory):
    svc, repo, bus, ledger, model = svc_factory(
        [reply_text(f"{PCCC} 번호를 확인했습니다."), submit_pccc(), reply_text(f"{PCCC}로 정정 신청했습니다.")],
        return_model=True,
    )
    t, asked = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=f"제 번호는 {PCCC} 입니다")
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="정정해 주세요")
    draft = repo.pending_draft(t.id)
    assert draft.text == "P2305****7201로 정정 신청했습니다."
    sent = svc.confirm_draft(t.id, draft.id, text=f"{PCCC} 번호를 관세사에 전달했습니다.")
    assert sent.edited and sent.text == "P2305****7201 번호를 관세사에 전달했습니다."
    stored = [m.text for m in repo.messages(t.id) if m.role == "agent" and m.kind == "normal"]
    assert stored == ["P2305****7201 번호를 확인했습니다.", "P2305****7201 번호를 관세사에 전달했습니다."]
    published = [
        e.payload["message"]["text"] for e in bus.since(0)
        if e.type.startswith("message.") and e.payload["message"]["role"] == "agent"
    ]
    assert all(PCCC not in text for text in published)
    assert [text for text in published if "P2305****7201" in text] == [  # auto-sent, drafted, confirmed
        "P2305****7201 번호를 확인했습니다.",
        "P2305****7201로 정정 신청했습니다.",
        "P2305****7201 번호를 관세사에 전달했습니다.",
    ]
    # The customer's own message stays as written: the next run still reads the real number.
    assert repo.messages(t.id)[0].text == asked.text == f"제 번호는 {PCCC} 입니다"
    assert f"客户：제 번호는 {PCCC} 입니다" in model.calls[1][1]["content"]


@pytest.mark.parametrize(
    "customer_id, channel, ack, holding",
    [
        ("C-KR-0412", "kakaotalk", ACK_KO, "담당자가 추가로 확인한 후 빠르게 답변드리겠습니다."),
        ("C-CN-3150", "wechat", ACK_ZH, "这个问题需要同事进一步确认，我们会尽快回复您。"),
    ],
    ids=["ko", "zh"],
)
def test_a_run_out_of_steps_drafts_the_holding_text_and_drops_its_writes(svc_factory, customer_id, channel, ack, holding):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "확인 필요"})], max_steps=1)
    t, _ = svc.customer_message(customer_id=customer_id, channel=channel, text="FWQD2611043")
    assert [(m.kind, m.status, m.text) for m in repo.messages(t.id) if m.role == "agent"] == [
        ("ack", "sent", ack), ("normal", "draft", holding)
    ]
    [run] = repo.runs(t.id)
    assert (run.outcome, run.decision) == ("max_steps", "draft") and ledger.staged(run.id) == []
    assert repo.get_ticket(t.id).status == "awaiting_confirmation"
    svc.confirm_draft(t.id, repo.pending_draft(t.id).id)
    # The handoff was dropped with the run, so confirming the holding text commits nothing; but the
    # text promises that a colleague follows up, so the ticket goes to staff.
    assert ledger.effects() == [] and repo.get_ticket(t.id).status == "needs_human"


def test_confirming_a_holding_draft_leaves_the_ticket_to_staff_even_when_edited(svc_factory):
    # Out of steps with reads only: nothing staged, no handoff. The run's outcome decides, not the text.
    svc, repo, bus, ledger = svc_factory([reply_tool("customs.get_progress", {"hbl": "FWQD2611043"})], max_steps=1)
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="통관 언제 되나요?")
    assert repo.runs(t.id)[0].outcome == "max_steps"
    sent = svc.confirm_draft(t.id, repo.pending_draft(t.id).id, text="담당자가 확인 후 다시 연락드리겠습니다.")
    assert sent.edited and ledger.effects() == [] and repo.get_ticket(t.id).status == "needs_human"


def test_confirming_a_handoff_draft_hands_the_ticket_to_staff(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "客户要求人工"}), reply_text("已为您转接人工客服。")])
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="帮我转人工")
    svc.confirm_draft(t.id, repo.pending_draft(t.id).id)
    assert [r.tool for r in ledger.effects()] == ["ticket.handoff"] and repo.get_ticket(t.id).status == "needs_human"


def test_a_refused_handoff_keeps_the_reply_a_draft_but_does_not_hand_off(svc_factory):
    # No reason given, so the call is refused and nothing is staged; the attempt alone rules out auto-send.
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {}), reply_text("확인해 보겠습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="상담원 연결해 주세요")
    draft = repo.pending_draft(t.id)
    assert repo.runs(t.id)[0].write_calls == ["ticket.handoff"] and ledger.staged(draft.run_id) == []
    sent = svc.confirm_draft(t.id, draft.id)
    assert (sent.status, sent.edited, sent.text) == ("sent", False, "확인해 보겠습니다.") and sent.sent_at
    assert ledger.effects() == [] and repo.get_ticket(t.id).status == "replied"


def test_an_llm_error_after_a_staged_write_discards_the_write(svc_factory):
    svc, repo, bus, ledger = svc_factory([submit_pccc(), LLMError(None, "ReadTimeout: timed out")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=PCCC)
    [run] = repo.runs(t.id)
    assert (run.outcome, run.decision, run.error) == ("error", "none", "LLM network error: ReadTimeout: timed out")
    assert ledger.staged(run.id) == [] and ledger.effects() == [] and repo.get_ticket(t.id).status == "needs_human"


def test_a_run_that_raises_hands_the_ticket_to_staff(svc_factory, caplog):
    svc, repo, bus, ledger = svc_factory([submit_pccc(), RuntimeError("boom"), reply_text("다시 확인했습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=PCCC)
    [run] = repo.runs(t.id)
    assert (run.status, run.outcome, run.decision, run.error) == ("finished", "error", "none", "RuntimeError: boom")
    assert [s["kind"] for s in run.steps] == ["model", "tool"] and ledger.staged(run.id) == []
    assert repo.get_ticket(t.id).status == "needs_human" and [m.role for m in repo.messages(t.id)] == ["customer"]
    *_, typing, finished = bus.since(0)
    assert (typing.type, typing.payload) == ("ticket.typing", {"typing": False})
    assert (finished.type, finished.payload["error"]) == ("run.finished", "RuntimeError: boom")
    assert "RuntimeError: boom" in caplog.text  # with its traceback
    # Nothing is left running: the next message gets a run of its own.
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="됐나요?")
    assert repo.messages(t.id)[-1].text == "다시 확인했습니다." and repo.get_ticket(t.id).status == "ai_replied"


def test_a_model_that_cannot_be_built_hands_the_ticket_to_staff(make_svc, caplog):
    attempts = []

    def broken_factory():
        attempts.append(1)
        raise RuntimeError("no model configured")

    svc, repo, bus, ledger = make_svc(broken_factory, InlineExecutor())
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="在吗")
    assert repo.get_ticket(t.id).status == "needs_human" and repo.runs(t.id) == []
    assert "no model configured" in caplog.text
    svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="在吗？")
    assert len(attempts) == 2 and repo.get_ticket(t.id).status == "needs_human"


def test_the_auto_reply_switch_is_read_when_each_run_ends(svc_factory, settings):
    svc, repo, bus, ledger = svc_factory([reply_text("통관 보류 중입니다."), reply_text("서류를 확인하고 있습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="통관 문의")
    settings.auto_reply_readonly = False
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="서류는요?")
    assert [(m.kind, m.status) for m in repo.messages(t.id) if m.role == "agent"] == [
        ("normal", "sent"), ("ack", "sent"), ("normal", "draft")
    ]
    assert [r.decision for r in repo.runs(t.id)] == ["auto_sent", "draft"]


def test_a_stale_run_leaves_no_trace_and_one_rerun_answers_every_new_message(svc_factory_manual, db):
    svc, repo, ex, model = svc_factory_manual([submit_pccc(), reply_text("제출했습니다."), reply_text("둘 다 확인했습니다.")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=PCCC)

    def two_more_messages():
        svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="두 번째")
        svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="세 번째")

    model.on_call(1, two_more_messages)  # once the first call's write is staged
    ex.run_next()
    [stale] = repo.runs(t.id)
    assert (stale.outcome, stale.decision) == ("final", "none") and ex.pending == 1
    assert Ledger(db).staged(stale.id) == [] and Ledger(db).effects() == []
    assert [m.role for m in repo.messages(t.id)] == ["customer"] * 3 and repo.get_ticket(t.id).status == "processing"
    ex.run_next()
    assert [m.text for m in repo.messages(t.id) if m.role == "agent"] == ["둘 다 확인했습니다."]
    user_block = model.calls[-1][1]["content"]
    assert "客户：두 번째" in user_block and user_block.endswith("【客户最新消息】\n세 번째")


def test_ticket_detail_has_every_message_and_customers_see_only_sent_ones(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "x"}), reply_text("草稿1"), reply_text("草稿2")], auto_reply=False)
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第一条")
    svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="第二条")
    detail = svc.ticket_detail(t.id)
    assert set(detail) == {"ticket", "customer", "messages", "runs"}
    assert detail["ticket"] == repo.get_ticket(t.id).to_dict() and detail["customer"]["name"] == "林晓"
    assert [(m["text"], m["status"]) for m in detail["messages"]] == [
        ("第一条", "sent"), (ACK_ZH, "sent"), ("草稿1", "discarded"),
        ("第二条", "sent"), (ACK_ZH, "sent"), ("草稿2", "draft"),
    ]
    assert [r["decision"] for r in detail["runs"]] == ["draft", "draft"]
    assert json.loads(json.dumps(detail, ensure_ascii=False)) == detail  # plain data, ready for JSON
    assert [m.text for m in svc.customer_messages(t.id)] == ["第一条", ACK_ZH, "第二条", ACK_ZH]
    with pytest.raises(KeyError):
        svc.ticket_detail("T-999999")


def test_only_a_pending_draft_of_that_ticket_can_be_confirmed_or_rewritten(svc_factory):
    svc, repo, bus, ledger = svc_factory([reply_tool("ticket.handoff", {"reason": "x"}), reply_text("转人工"), reply_text("你好")], auto_reply=False)
    t, asked = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="帮我转人工")
    other, _ = svc.customer_message(customer_id="C-CN-3150", channel="web", text="在吗")
    draft = repo.pending_draft(t.id)
    with pytest.raises(KeyError):
        svc.confirm_draft(other.id, draft.id)  # a message of another ticket
    with pytest.raises(NotADraftError):
        svc.regenerate_draft(t.id, asked.id)  # a customer message
    svc.confirm_draft(t.id, draft.id)
    published = len(bus.since(0))
    with pytest.raises(NotADraftError):
        svc.confirm_draft(t.id, draft.id)
    with pytest.raises(NotADraftError):
        svc.regenerate_draft(t.id, draft.id)
    assert len(bus.since(0)) == published and len(ledger.effects()) == 1


def test_a_rewrite_is_always_a_draft_even_with_auto_reply_on(svc_factory):
    svc, repo, bus, ledger = svc_factory(
        [reply_tool("ticket.handoff", {"reason": "x"}), reply_text("转人工"), reply_text("新草稿"), reply_text("在的")]
    )
    t, _ = svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="帮我转人工")
    svc.regenerate_draft(t.id, repo.pending_draft(t.id).id)
    # The rewrite only read data and auto-reply is on, yet it waits for staff; the cycle had its ack.
    assert [(m.kind, m.status, m.text) for m in repo.messages(t.id) if m.role == "agent"] == [
        ("ack", "sent", ACK_ZH), ("normal", "discarded", "转人工"), ("normal", "draft", "新草稿")
    ]
    assert repo.runs(t.id)[-1].decision == "draft" and repo.get_ticket(t.id).status == "awaiting_confirmation"
    # A customer message starts a run of its own, which keeps the decision table.
    svc.customer_message(customer_id="C-CN-3150", channel="wechat", text="还在吗")
    assert (repo.messages(t.id)[-1].text, repo.messages(t.id)[-1].status) == ("在的", "sent")
    assert repo.get_ticket(t.id).status == "ai_replied"


SEEDS = [
    {
        "id": "T-001040", "customer_id": "C-KR-1088", "channel": "email",
        "subject": "FWICN2611088 위치 문의", "status": "ai_replied",
        "messages": [
            {"role": "customer", "text": "FWICN2611088 지금 어디쯤인가요?"},
            {"role": "agent", "text": "인천항에서 선적을 기다리고 있습니다."},
        ],
    },
    {
        "id": "T-001041", "customer_id": "C-CN-3150", "channel": "wechat",
        "subject": "包裹在海关好几天了", "status": "ai_replied",
        "messages": [
            {"role": "customer", "text": "我的包裹为什么在海关好几天了？"},
            {"role": "agent", "text": "您的包裹正在接受海关查验。"},
        ],
    },
]
SEEDED_T_001040 = [
    ("customer", "normal", "sent", "FWICN2611088 지금 어디쯤인가요?"),
    ("agent", "normal", "sent", "인천항에서 선적을 기다리고 있습니다."),
]


@pytest.fixture
def seed_path(tmp_path):
    path = tmp_path / "seed_tickets.json"
    path.write_text(json.dumps(SEEDS, ensure_ascii=False), encoding="utf-8")
    return path


def test_reset_demo_empties_the_database_and_loads_the_seed_tickets(svc_factory, seed_path):
    svc, repo, bus, ledger = svc_factory([submit_pccc(), reply_text("제출했습니다."), reply_text("네")])
    t, _ = svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=PCCC)
    assert t.id == "T-001040"
    svc.confirm_draft(t.id, repo.pending_draft(t.id).id)
    assert len(ledger.effects()) == 1
    svc.reset_demo(seed_path)
    assert sorted((x.id, x.customer_id, x.channel, x.subject, x.status) for x in repo.list_tickets()) == [
        ("T-001040", "C-KR-1088", "email", "FWICN2611088 위치 문의", "ai_replied"),
        ("T-001041", "C-CN-3150", "wechat", "包裹在海关好几天了", "ai_replied"),
    ]
    assert [(m.role, m.kind, m.status, m.text) for m in repo.messages("T-001040")] == SEEDED_T_001040
    assert repo.runs("T-001040") == [] and ledger.effects() == []
    assert svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="안녕하세요")[0].id == "T-001042"


def test_reset_demo_drops_runs_that_were_under_way_or_queued(svc_factory_manual, seed_path, db):
    svc, repo, ex, model = svc_factory_manual([submit_pccc(), reply_text("제출했습니다.")])
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text=PCCC)  # T-001040, as is the first seed
    model.on_call(0, lambda: svc.reset_demo(seed_path))
    ex.run_next()  # the run goes on after the reset and stages its write, but writes nothing else
    events = svc._bus.since(0)
    reset_seq = next(event.seq for event in events if event.type == "demo.reset")
    assert svc._bus.since(reset_seq) == []
    assert [(m.role, m.kind, m.status, m.text) for m in repo.messages("T-001040")] == SEEDED_T_001040
    assert db.query("SELECT status FROM ledger") == [{"status": "discarded"}] and ex.pending == 0
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="안녕하세요")  # T-001042
    svc.reset_demo(seed_path)
    calls = len(model.calls)
    ex.run_next()  # queued before the reset: never starts
    assert len(model.calls) == calls and repo.get_ticket("T-001042") is None and ex.pending == 0


def test_reset_demo_publishes_demo_reset_once_the_seeds_are_in(svc_factory, seed_path, monkeypatch):
    svc, repo, bus, ledger = svc_factory([reply_text("네")])
    svc.customer_message(customer_id="C-KR-0412", channel="kakaotalk", text="안녕하세요")  # T-001040, not a seed
    tickets_when_published = []
    publish = bus.publish

    def spy(event_type, ticket_id, payload):
        if event_type == "demo.reset":
            tickets_when_published.append([(x.id, x.customer_id) for x in sorted(repo.list_tickets(), key=lambda x: x.id)])
        return publish(event_type, ticket_id, payload)

    monkeypatch.setattr(bus, "publish", spy)
    before = bus.last_seq
    svc.reset_demo(seed_path)
    assert [(e.type, e.ticket_id, e.payload) for e in bus.since(before)] == [("demo.reset", None, {})]
    assert tickets_when_published == [[("T-001040", "C-KR-1088"), ("T-001041", "C-CN-3150")]]
