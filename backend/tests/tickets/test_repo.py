import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest

from forwarder.tickets.models import CHANNELS, TICKET_STATUSES, Message, RunRecord, Ticket
from forwarder.tickets.repo import TicketRepo

# Earlier than anything a test creates: tests reset updated_at to it to see which calls move it.
LONG_AGO = "2000-01-01T00:00:00.000+09:00"


@pytest.fixture
def repo(db):
    return TicketRepo(db)


def set_updated_at(db, ticket_id, value):
    db.execute("UPDATE tickets SET updated_at = ? WHERE id = ?", (value, ticket_id))


def test_ticket_ids_increment(repo):
    a = repo.create_ticket(customer_id="C-1", channel="web", subject="a")
    b = repo.create_ticket(customer_id="C-1", channel="web", subject="b")
    assert a.id == "T-001040" and b.id == "T-001041"


def test_pending_draft_and_visibility(repo):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    repo.add_message(ticket_id=t.id, role="customer", text="你好")
    d = repo.add_message(ticket_id=t.id, role="agent", text="草稿", status="draft", run_id="R-1")
    assert repo.pending_draft(t.id).id == d.id and d.visible_to_customer is False
    repo.update_message(d.id, status="discarded")
    assert repo.pending_draft(t.id) is None


def test_run_record_roundtrip(repo):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    repo.create_run(run_id="R-1", ticket_id=t.id, model="mock")
    r = repo.finish_run("R-1", outcome="final", decision="auto_sent", steps=[{"index": 0}], usage={"prompt_tokens": 3}, write_calls=[], error=None)
    assert r.status == "finished" and r.steps == [{"index": 0}] and r.usage["prompt_tokens"] == 3


def test_status_and_channel_values():
    assert TICKET_STATUSES == ("processing", "ai_replied", "awaiting_confirmation", "replied", "needs_human")
    assert CHANNELS == ("web", "wechat", "kakaotalk", "email", "web_form")


def test_new_ticket_starts_processing_and_reads_back(repo):
    t = repo.create_ticket(customer_id="C-KR-0412", channel="kakaotalk", subject="반입신고 문의")
    assert t == Ticket("T-001040", "C-KR-0412", "kakaotalk", "반입신고 문의", "processing", t.created_at, t.created_at)
    assert t.created_at.endswith("+09:00")
    assert datetime.fromisoformat(t.created_at).utcoffset() == timedelta(hours=9)
    assert repo.get_ticket(t.id) == t and repo.get_ticket("T-999999") is None


def test_generated_ids_follow_the_highest_number_in_use(repo):
    # Seed tickets bring their own ids. A generated id is never below T-001040, and ids that are
    # not "T-" + 6 digits take no part in the numbering.
    repo.create_ticket(customer_id="C-1", channel="web", subject="old", ticket_id="T-000007")
    assert repo.create_ticket(customer_id="C-1", channel="web", subject="a").id == "T-001040"
    repo.create_ticket(customer_id="C-2", channel="email", subject="seed", ticket_id="T-001050")
    repo.create_ticket(customer_id="C-3", channel="web", subject="legacy", ticket_id="legacy-2000")
    assert repo.create_ticket(customer_id="C-1", channel="web", subject="b").id == "T-001051"


def test_generated_ids_stay_unique_across_threads(repo):
    def open_tickets(worker):
        return [repo.create_ticket(customer_id=f"C-{worker}", channel="web", subject="s").id for _ in range(10)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = [ticket_id for batch in pool.map(open_tickets, range(8)) for ticket_id in batch]
    assert sorted(ids) == [f"T-{n:06d}" for n in range(1040, 1120)]


def test_list_tickets_puts_the_latest_update_first(repo, db):
    a, b, c = [repo.create_ticket(customer_id="C-1", channel="web", subject=s) for s in "abc"]
    set_updated_at(db, a.id, "2026-11-20T09:00:02.000+09:00")
    set_updated_at(db, b.id, "2026-11-20T09:00:01.000+09:00")
    set_updated_at(db, c.id, "2026-11-20T09:00:03.000+09:00")
    assert [t.id for t in repo.list_tickets()] == [c.id, a.id, b.id]


def test_status_and_message_changes_touch_the_ticket(repo, db):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    set_updated_at(db, t.id, LONG_AGO)
    changed = repo.set_status(t.id, "needs_human")
    assert changed == Ticket(t.id, "C-1", "web", "s", "needs_human", t.created_at, changed.updated_at)
    assert changed.updated_at > LONG_AGO and repo.get_ticket(t.id) == changed

    set_updated_at(db, t.id, LONG_AGO)
    m = repo.add_message(ticket_id=t.id, role="customer", text="还在吗？")
    assert repo.get_ticket(t.id).updated_at == m.created_at

    set_updated_at(db, t.id, LONG_AGO)
    repo.update_message(m.id, text="还在吗")
    assert repo.get_ticket(t.id).updated_at > LONG_AGO


def test_latest_ticket_for_is_the_newest_on_that_channel(repo):
    assert repo.latest_ticket_for("C-1", "web") is None
    old = repo.create_ticket(customer_id="C-1", channel="web", subject="old")
    wechat = repo.create_ticket(customer_id="C-1", channel="wechat", subject="w")
    repo.create_ticket(customer_id="C-2", channel="web", subject="another customer")
    new = repo.create_ticket(customer_id="C-1", channel="web", subject="new")
    repo.set_status(old.id, "replied")  # a later update does not make the older ticket the latest
    assert repo.latest_ticket_for("C-1", "web") == new
    assert repo.latest_ticket_for("C-1", "wechat") == wechat
    assert repo.latest_ticket_for("C-2", "wechat") is None


def test_messages_keep_their_fields_and_order(repo):
    t = repo.create_ticket(customer_id="C-KR-0412", channel="kakaotalk", subject="s")
    other = repo.create_ticket(customer_id="C-CN-3150", channel="wechat", subject="s")
    asked = repo.add_message(ticket_id=t.id, role="customer", text="FWQD2611043 반입신고에서 멈췄어요")
    repo.add_message(ticket_id=other.id, role="customer", text="在吗")
    ack = repo.add_message(ticket_id=t.id, role="agent", kind="ack", text="메시지를 확인했습니다.")
    draft = repo.add_message(ticket_id=t.id, role="agent", text="초안", status="draft", run_id="R-1")
    # A message added as sent is sent at once; a draft has no sent_at yet.
    assert asked == Message(
        asked.id, t.id, "customer", "normal", "FWQD2611043 반입신고에서 멈췄어요", "sent", None, False,
        asked.created_at, asked.created_at,
    )
    assert (draft.kind, draft.status, draft.run_id, draft.edited, draft.sent_at) == ("normal", "draft", "R-1", False, None)
    assert [m.visible_to_customer for m in (asked, ack, draft)] == [True, True, False]
    assert repo.messages(t.id) == [asked, ack, draft] and repo.messages("T-999999") == []


def test_pending_draft_is_the_newest_draft_of_that_ticket(repo):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    other = repo.create_ticket(customer_id="C-2", channel="web", subject="s")
    repo.add_message(ticket_id=other.id, role="agent", text="별건", status="draft", run_id="R-9")
    assert repo.pending_draft(t.id) is None
    repo.add_message(ticket_id=t.id, role="agent", text="첫 초안", status="draft", run_id="R-1")
    second = repo.add_message(ticket_id=t.id, role="agent", text="둘째 초안", status="draft", run_id="R-2")
    assert repo.pending_draft(t.id) == second


def test_update_message_changes_only_the_given_fields(repo, db):
    t = repo.create_ticket(customer_id="C-KR-0412", channel="kakaotalk", subject="s")
    draft = repo.add_message(ticket_id=t.id, role="agent", text="제출했습니다.", status="draft", run_id="R-1")
    sent = repo.update_message(
        draft.id, status="sent", text="정정된 번호를 전달했습니다.", edited=True, sent_at="2026-11-20T10:00:00.000+09:00"
    )
    assert sent == Message(
        draft.id, t.id, "agent", "normal", "정정된 번호를 전달했습니다.", "sent", "R-1", True,
        draft.created_at, "2026-11-20T10:00:00.000+09:00",
    )
    assert repo.messages(t.id) == [sent] and repo.update_message(draft.id) == sent
    assert db.query_one("SELECT edited FROM messages WHERE id = ?", (draft.id,)) == {"edited": 1}


def test_runs_are_stored_as_readable_json_and_returned_as_stored(repo, db):
    t = repo.create_ticket(customer_id="C-KR-0412", channel="kakaotalk", subject="s")
    started = repo.create_run(run_id="R-1", ticket_id=t.id, model="deepseek-flash")
    assert started == RunRecord(
        "R-1", t.id, "running", None, None, "deepseek-flash", [], {}, [], None, started.started_at, None
    )
    assert datetime.fromisoformat(started.started_at).utcoffset() == timedelta(hours=9)

    steps = [{"index": 0, "kind": "tool", "data": {"observation": {"hold": "통관 보류", "备注": "待补"}}}]
    finished = repo.finish_run(
        "R-1", outcome="final", decision="draft", steps=steps, usage={"prompt_tokens": 3},
        write_calls=["customs.submit_pccc"], error=None,
    )
    steps[0]["index"] = 7  # the record holds what was stored, not the caller's list
    assert finished == RunRecord(
        "R-1", t.id, "finished", "final", "draft", "deepseek-flash",
        [{"index": 0, "kind": "tool", "data": {"observation": {"hold": "통관 보류", "备注": "待补"}}}],
        {"prompt_tokens": 3}, ["customs.submit_pccc"], None, started.started_at, finished.finished_at,
    )
    assert datetime.fromisoformat(finished.finished_at).utcoffset() == timedelta(hours=9)
    # Hangul and Chinese are stored as they are, not as \u escapes.
    row = db.query_one("SELECT steps, write_calls FROM runs WHERE id = ?", ("R-1",))
    assert "통관 보류" in row["steps"] and "备注" in row["steps"]
    assert json.loads(row["write_calls"]) == ["customs.submit_pccc"]

    later = repo.create_run(run_id="R-2", ticket_id=t.id, model="mock")
    other = repo.create_ticket(customer_id="C-2", channel="web", subject="s")
    repo.create_run(run_id="R-3", ticket_id=other.id, model="mock")
    assert repo.runs(t.id) == [finished, later]


def test_unknown_values_are_refused(repo, db):
    with pytest.raises(ValueError):
        repo.create_ticket(customer_id="C-1", channel="fax", subject="s")
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    with pytest.raises(ValueError):
        repo.set_status(t.id, "closed")
    for bad in ({"role": "bot"}, {"kind": "note"}, {"status": "pending"}):
        with pytest.raises(ValueError):
            repo.add_message(**{"ticket_id": t.id, "role": "agent", "text": "x", **bad})
    m = repo.add_message(ticket_id=t.id, role="customer", text="x")
    with pytest.raises(ValueError):
        repo.update_message(m.id, status="deleted")
    repo.create_run(run_id="R-1", ticket_id=t.id, model="mock")
    with pytest.raises(ValueError):
        repo.finish_run("R-1", outcome="final", decision="sent", steps=[], usage={}, write_calls=[], error=None)
    # Nothing the refused calls asked for was written.
    assert [x.id for x in repo.list_tickets()] == [t.id] and repo.get_ticket(t.id).status == "processing"
    assert repo.messages(t.id) == [m] and [r.status for r in repo.runs(t.id)] == ["running"]


def test_missing_records_raise_key_error(repo, db):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    with pytest.raises(KeyError):
        repo.set_status("T-999999", "replied")
    with pytest.raises(KeyError):
        repo.add_message(ticket_id="T-999999", role="customer", text="x")
    with pytest.raises(KeyError):
        repo.update_message(999, status="sent")
    with pytest.raises(KeyError):
        repo.create_run(run_id="R-1", ticket_id="T-999999", model="mock")
    with pytest.raises(KeyError):
        repo.finish_run("R-404", outcome="error", decision="none", steps=[], usage={}, write_calls=[], error="x")
    assert db.query("SELECT id FROM messages") == [] and db.query("SELECT id FROM runs") == []
    assert repo.list_tickets() == [t]


def test_to_dict_gives_every_field(repo):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    m = repo.add_message(ticket_id=t.id, role="customer", text="안녕하세요")
    r = repo.create_run(run_id="R-1", ticket_id=t.id, model="mock")
    t = repo.get_ticket(t.id)
    assert t.to_dict() == {
        "id": t.id, "customer_id": "C-1", "channel": "web", "subject": "s", "status": "processing",
        "created_at": t.created_at, "updated_at": t.updated_at,
    }
    assert m.to_dict() == {
        "id": m.id, "ticket_id": t.id, "role": "customer", "kind": "normal", "text": "안녕하세요",
        "status": "sent", "run_id": None, "edited": False, "created_at": m.created_at, "sent_at": m.created_at,
    }
    assert r.to_dict() == {
        "id": "R-1", "ticket_id": t.id, "status": "running", "outcome": None, "decision": None,
        "model": "mock", "steps": [], "usage": {}, "write_calls": [], "error": None,
        "started_at": r.started_at, "finished_at": None,
    }


def test_clear_deletes_every_ticket_message_and_run(repo):
    t = repo.create_ticket(customer_id="C-1", channel="web", subject="s")
    repo.add_message(ticket_id=t.id, role="customer", text="x")
    repo.create_run(run_id="R-1", ticket_id=t.id, model="mock")
    repo.clear()
    assert repo.list_tickets() == [] and repo.messages(t.id) == [] and repo.runs(t.id) == []
    assert repo.create_ticket(customer_id="C-1", channel="web", subject="s").id == "T-001040"
