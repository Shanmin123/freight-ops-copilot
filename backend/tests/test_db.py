from concurrent.futures import ThreadPoolExecutor

import pytest

from forwarder.db import Database

INSERT_LEDGER = (
    "INSERT INTO ledger(namespace,ticket_id,tool,payload,status,created_at,updated_at) "
    "VALUES ('n','T',?,'{}','staged','t','t')"
)


def ledger_tools(db):
    return [row["tool"] for row in db.query("SELECT tool FROM ledger ORDER BY id")]


def test_schema_and_roundtrip_unicode(tmp_path):
    db = Database(tmp_path / "t.db"); db.init_schema()
    db.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?,?)", ("T-1", "C-1", "web", "반입신고 문의", "processing", "t", "t"))
    assert db.query_one("SELECT subject FROM tickets WHERE id=?", ("T-1",))["subject"] == "반입신고 문의"


def test_reset_clears_rows(tmp_path):
    db = Database(tmp_path / "t.db"); db.init_schema()
    db.execute("INSERT INTO ledger(namespace,ticket_id,tool,payload,status,created_at,updated_at) VALUES ('n','T','x','{}','staged','t','t')")
    db.reset()
    assert db.query("SELECT * FROM ledger") == []


def test_execute_returns_lastrowid_and_rows_are_dicts(db):
    assert db.execute(INSERT_LEDGER, ("a",)) == 1
    assert db.execute(INSERT_LEDGER, ("b",)) == 2
    assert db.query("SELECT id, tool FROM ledger ORDER BY id") == [{"id": 1, "tool": "a"}, {"id": 2, "tool": "b"}]
    assert db.query_one("SELECT id FROM ledger WHERE tool=?", ("missing",)) is None


def test_opens_in_wal_mode_and_creates_parent_dirs(tmp_path):
    db = Database(tmp_path / "var" / "nested" / "t.db")
    assert db.query_one("PRAGMA journal_mode") == {"journal_mode": "wal"}


def test_transaction_commits_on_success_and_rolls_back_on_error(db, tmp_path):
    with db.transaction():
        db.execute(INSERT_LEDGER, ("kept",))
    with pytest.raises(RuntimeError):
        with db.transaction():
            db.execute(INSERT_LEDGER, ("dropped",))
            raise RuntimeError("boom")
    # A second connection sees only what was committed.
    assert ledger_tools(Database(tmp_path / "t.db")) == ["kept"]


def test_nested_transaction_failure_undoes_only_the_inner_block(db):
    with db.transaction():
        db.execute(INSERT_LEDGER, ("outer",))
        with pytest.raises(RuntimeError):
            with db.transaction():
                db.execute(INSERT_LEDGER, ("inner",))
                raise RuntimeError("boom")
    assert ledger_tools(db) == ["outer"]


def test_threads_can_share_one_database(db):
    def write_batch(worker):
        for i in range(20):
            with db.transaction():
                db.execute(INSERT_LEDGER, (f"w{worker}-{i}",))

    with ThreadPoolExecutor(max_workers=8) as pool:
        for future in [pool.submit(write_batch, worker) for worker in range(8)]:
            future.result()
    assert db.query_one("SELECT COUNT(*) AS n FROM ledger") == {"n": 160}


def test_reset_clears_every_table_and_keeps_the_schema(db):
    db.execute("INSERT INTO tickets VALUES ('T-1','C-1','web','s','processing','t','t')")
    db.execute("INSERT INTO messages(ticket_id,role,kind,text,status,edited,created_at) VALUES ('T-1','customer','normal','hi','sent',0,'t')")
    db.execute("INSERT INTO runs(id,ticket_id,status,model,started_at) VALUES ('R-1','T-1','running','mock','t')")
    db.execute(INSERT_LEDGER, ("before",))
    db.reset()
    for table in ("tickets", "messages", "runs", "ledger"):
        assert db.query(f"SELECT * FROM {table}") == []
    db.execute(INSERT_LEDGER, ("after",))
    assert ledger_tools(db) == ["after"]
