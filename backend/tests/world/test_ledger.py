import json
from datetime import datetime, timedelta

from forwarder.world.ledger import Ledger, LedgerRecord


def test_staged_visible_only_in_own_namespace(db):
    led = Ledger(db)
    led.stage(namespace="R-1", ticket_id="T-1", tool="customs.submit_pccc", payload={"hbl": "FWQD2611043"})
    assert [r.tool for r in led.effects("R-1")] == ["customs.submit_pccc"]
    assert led.effects("R-2") == [] and led.effects() == []


def test_commit_makes_record_global(db):
    led = Ledger(db); led.stage(namespace="R-1", ticket_id="T-1", tool="x", payload={})
    assert led.commit("R-1") == 1
    assert len(led.effects()) == 1 and led.staged("R-1") == []


def test_discard_removes_from_effects(db):
    led = Ledger(db); led.stage(namespace="R-1", ticket_id="T-1", tool="x", payload={})
    assert led.discard("R-1") == 1 and led.effects("R-1") == []


def test_stage_returns_the_stored_record_and_keeps_text_readable(db):
    led = Ledger(db)
    payload = {"job_id": "WJ-2611-031", "cartons": ["C-014"], "note": "재포장 부탁드립니다，谢谢"}
    record = led.stage(namespace="R-1", ticket_id="T-7", tool="warehouse.request_action", payload=payload)
    assert isinstance(record.id, int)
    assert record == LedgerRecord(
        record.id, "R-1", "T-7", "warehouse.request_action", payload, "staged", record.created_at
    )
    assert record.created_at.endswith("+09:00")
    assert datetime.fromisoformat(record.created_at).utcoffset() == timedelta(hours=9)
    assert led.staged("R-1") == [record]
    # The record is a snapshot of what was stored, not the caller's dict.
    payload["cartons"].append("C-027")
    assert record.payload["cartons"] == ["C-014"]
    # Stored as JSON with Hangul and Chinese kept as they are, not as \u escapes.
    row = db.query_one("SELECT payload FROM ledger WHERE id = ?", (record.id,))
    assert "재포장 부탁드립니다，谢谢" in row["payload"]
    assert json.loads(row["payload"]) == record.payload


def test_commit_and_discard_change_only_that_namespaces_staged_records(db):
    led = Ledger(db)
    for namespace, tool in [("R-1", "a"), ("R-2", "b"), ("R-1", "c")]:
        led.stage(namespace=namespace, ticket_id="T-1", tool=tool, payload={})
    assert led.commit("R-1") == 2
    # Committed records reach every namespace; R-2 also sees its own staged record. Oldest first.
    assert [(r.tool, r.status) for r in led.effects("R-2")] == [
        ("a", "committed"), ("b", "staged"), ("c", "committed")
    ]
    # Records that have left "staged" are never touched again.
    assert led.discard("R-1") == 0 and led.commit("R-1") == 0
    assert led.discard("R-2") == 1 and led.commit("R-2") == 0
    assert [r.tool for r in led.effects()] == ["a", "c"] and led.effects("R-2") == led.effects()
    assert db.query("SELECT tool, status FROM ledger ORDER BY id") == [
        {"tool": "a", "status": "committed"},
        {"tool": "b", "status": "discarded"},
        {"tool": "c", "status": "committed"},
    ]


def test_clear_deletes_records_of_every_status(db):
    led = Ledger(db)
    for namespace in ("R-1", "R-2", "R-3"):
        led.stage(namespace=namespace, ticket_id="T-1", tool="x", payload={})
    led.commit("R-1")
    led.discard("R-2")
    led.clear()
    assert db.query("SELECT id FROM ledger") == []
