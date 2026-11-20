"""SQLite wrapper: one shared connection, schema creation, and reset."""

import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

Params = Sequence[Any] | Mapping[str, Any]

_TABLES: dict[str, str] = {
    "tickets": """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY NOT NULL,
            customer_id TEXT,
            channel TEXT,
            subject TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )""",
    "messages": """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            role TEXT,
            kind TEXT,
            text TEXT,
            status TEXT,
            run_id TEXT,
            edited INTEGER,
            created_at TEXT,
            sent_at TEXT
        )""",
    "runs": """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY NOT NULL,
            ticket_id TEXT,
            status TEXT,
            outcome TEXT,
            decision TEXT,
            model TEXT,
            steps TEXT,
            usage TEXT,
            write_calls TEXT,
            error TEXT,
            started_at TEXT,
            finished_at TEXT
        )""",
    "ledger": """
        CREATE TABLE IF NOT EXISTS ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            namespace TEXT,
            ticket_id TEXT,
            tool TEXT,
            payload TEXT,
            status TEXT,
            created_at TEXT,
            updated_at TEXT
        )""",
}


def _dict_row(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row)}


class Database:
    """One sqlite3 connection shared by all threads. Every operation runs under the lock."""

    def __init__(self, path: Path | str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Re-entrant, so execute()/query() can be called inside transaction() on the same thread.
        self._lock = threading.RLock()
        # isolation_level=None: each statement commits on its own unless a transaction() block is open.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = _dict_row
        self._conn.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql: str, params: Params = ()) -> int:
        """Run one statement and return the cursor's lastrowid."""
        with self._lock:
            return self._conn.execute(sql, params).lastrowid

    def query(self, sql: str, params: Params = ()) -> list[dict[str, Any]]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Params = ()) -> dict[str, Any] | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        """Commit everything in the block together, or roll it all back if the block raises.

        Other threads wait until the block ends. A nested block is a savepoint of its own: if it
        raises, only its statements are undone and the outer block carries on.
        """
        with self._lock:
            # Savepoint names may repeat; ROLLBACK TO and RELEASE act on the innermost "tx".
            # Releasing the outermost savepoint commits the transaction.
            self._conn.execute("SAVEPOINT tx")
            try:
                yield self
            except BaseException:
                self._conn.execute("ROLLBACK TO tx")
                self._conn.execute("RELEASE tx")
                raise
            self._conn.execute("RELEASE tx")

    def init_schema(self) -> None:
        """Create any missing tables."""
        with self.transaction():
            for ddl in _TABLES.values():
                self._conn.execute(ddl)

    def reset(self) -> None:
        """Delete every row from every table. The tables themselves stay."""
        with self.transaction():
            for table in _TABLES:
                self._conn.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        """Release the connection after all its users have stopped."""
        with self._lock:
            self._conn.close()
