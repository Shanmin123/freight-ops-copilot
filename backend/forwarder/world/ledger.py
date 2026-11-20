"""Write ledger: write-tool effects staged under one run's namespace, then committed or discarded."""

import json
from dataclasses import dataclass
from datetime import datetime

from forwarder.db import Database

from .data import WORLD_TZ

STAGED = "staged"
COMMITTED = "committed"
DISCARDED = "discarded"


@dataclass(frozen=True)
class LedgerRecord:
    id: int
    namespace: str  # the run that staged it
    ticket_id: str
    tool: str  # dotted name, e.g. "customs.submit_pccc"
    payload: dict
    status: str  # STAGED, COMMITTED or DISCARDED
    created_at: str  # when it was staged


class Ledger:
    """Staged records apply only inside their own namespace; committed records apply everywhere.

    Discarded records apply nowhere. commit() and discard() never touch a record that has left STAGED.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def stage(self, *, namespace: str, ticket_id: str, tool: str, payload: dict) -> LedgerRecord:
        """Store a staged record and return it as stored (payload as read back from its JSON)."""
        encoded = json.dumps(payload, ensure_ascii=False)
        now = _now()
        record_id = self._db.execute(
            "INSERT INTO ledger(namespace, ticket_id, tool, payload, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (namespace, ticket_id, tool, encoded, STAGED, now, now),
        )
        return LedgerRecord(record_id, namespace, ticket_id, tool, json.loads(encoded), STAGED, now)

    def commit(self, namespace: str) -> int:
        """Turn all of namespace's staged records into committed ones at once; return how many."""
        return self._settle(namespace, COMMITTED)

    def discard(self, namespace: str) -> int:
        """Turn all of namespace's staged records into discarded ones at once; return how many."""
        return self._settle(namespace, DISCARDED)

    def effects(self, namespace: str | None = None) -> list[LedgerRecord]:
        """Every committed record, plus namespace's staged ones if a namespace is given; oldest first."""
        if namespace is None:
            return self._select("status = ?", (COMMITTED,))
        return self._select(
            "status = ? OR (status = ? AND namespace = ?)", (COMMITTED, STAGED, namespace)
        )

    def staged(self, namespace: str) -> list[LedgerRecord]:
        """namespace's records that are still staged, oldest first."""
        return self._select("status = ? AND namespace = ?", (STAGED, namespace))

    def clear(self) -> None:
        """Delete every record, whatever its status, so the world is its seed data again (demo reset)."""
        self._db.execute("DELETE FROM ledger")

    def _settle(self, namespace: str, status: str) -> int:
        # One UPDATE moves every staged row of the namespace; rows already committed or discarded
        # never match. The transaction keeps other statements from running between it and changes().
        with self._db.transaction():
            self._db.execute(
                "UPDATE ledger SET status = ?, updated_at = ? WHERE namespace = ? AND status = ?",
                (status, _now(), namespace, STAGED),
            )
            return self._db.query_one("SELECT changes() AS n")["n"]

    def _select(self, where: str, params: tuple) -> list[LedgerRecord]:
        rows = self._db.query(
            "SELECT id, namespace, ticket_id, tool, payload, status, created_at FROM ledger "
            f"WHERE {where} ORDER BY id",
            params,
        )
        return [LedgerRecord(**{**row, "payload": json.loads(row["payload"])}) for row in rows]


def _now() -> str:
    return datetime.now(WORLD_TZ).isoformat(timespec="seconds")
