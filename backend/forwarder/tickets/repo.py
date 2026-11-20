"""TicketRepo: tickets, their messages and the runs that answered them, stored in SQLite."""

import json
from dataclasses import fields
from typing import get_args

from forwarder.db import Database

from .models import (
    CHANNELS,
    TICKET_STATUSES,
    Message,
    MessageKind,
    MessageRole,
    MessageStatus,
    RunDecision,
    RunRecord,
    Ticket,
    now_iso,
)

_FIRST_TICKET_NUMBER = 1040  # the first generated id is T-001040

# Each SELECT reads exactly the record's fields, so a row unpacks straight into its dataclass.
_TICKET_COLUMNS = ", ".join(field.name for field in fields(Ticket))
_MESSAGE_COLUMNS = ", ".join(field.name for field in fields(Message))
_RUN_COLUMNS = ", ".join(field.name for field in fields(RunRecord))
_RUN_JSON_FIELDS = ("steps", "usage", "write_calls")


class TicketRepo:
    """Stores tickets, messages and runs; every method returns records as they were stored.

    An unknown channel, status, role, kind or decision raises ValueError, and a ticket, message or
    run that does not exist raises KeyError (get_ticket, latest_ticket_for and pending_draft return
    None instead). A ticket or run id that is already taken raises sqlite3.IntegrityError. A call
    that raises writes nothing.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # Tickets

    def create_ticket(
        self, *, customer_id: str, channel: str, subject: str, ticket_id: str | None = None
    ) -> Ticket:
        """Open a ticket in status "processing".

        Without ticket_id the id is "T-" + 6 digits: the number after the highest such id in use,
        and never below T-001040. Seed tickets pass their own ids.
        """
        _check(channel, CHANNELS, "channel")
        # One transaction, so no other thread can take the same number in between.
        with self._db.transaction():
            if ticket_id is None:
                ticket_id = self._next_ticket_id()
            now = now_iso()
            self._db.execute(
                "INSERT INTO tickets(id, customer_id, channel, subject, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticket_id, customer_id, channel, subject, "processing", now, now),
            )
            return self._ticket(ticket_id)

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        row = self._db.query_one(f"SELECT {_TICKET_COLUMNS} FROM tickets WHERE id = ?", (ticket_id,))
        return None if row is None else Ticket(**row)

    def list_tickets(self) -> list[Ticket]:
        """Every ticket, the most recently updated first (ties: the higher id first)."""
        rows = self._db.query(
            f"SELECT {_TICKET_COLUMNS} FROM tickets ORDER BY updated_at DESC, id DESC"
        )
        return [Ticket(**row) for row in rows]

    def latest_ticket_for(self, customer_id: str, channel: str) -> Ticket | None:
        """The customer's most recently opened ticket on channel, however long ago it was updated."""
        row = self._db.query_one(
            f"SELECT {_TICKET_COLUMNS} FROM tickets WHERE customer_id = ? AND channel = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (customer_id, channel),
        )
        return None if row is None else Ticket(**row)

    def set_status(self, ticket_id: str, status: str) -> Ticket:
        """Change the ticket's status; its updated_at becomes now."""
        _check(status, TICKET_STATUSES, "ticket status")
        with self._db.transaction():
            self._touch(ticket_id, now_iso(), status=status)
            return self._ticket(ticket_id)

    # Messages

    def add_message(
        self,
        *,
        ticket_id: str,
        role: str,
        text: str,
        kind: str = "normal",
        status: str = "sent",
        run_id: str | None = None,
    ) -> Message:
        """Append a message to the ticket; the ticket's updated_at becomes the message's created_at.

        A message added as "sent" is sent at once (sent_at = created_at); any other has sent_at None.
        """
        _check(role, get_args(MessageRole), "message role")
        _check(kind, get_args(MessageKind), "message kind")
        _check(status, get_args(MessageStatus), "message status")
        with self._db.transaction():
            now = now_iso()  # taken under the lock, so created_at follows the id order
            self._touch(ticket_id, now)
            message_id = self._db.execute(
                "INSERT INTO messages(ticket_id, role, kind, text, status, run_id, edited, created_at, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (ticket_id, role, kind, text, status, run_id, now, now if status == "sent" else None),
            )
            return self._message(message_id)

    def update_message(
        self,
        message_id: int,
        *,
        status: str | None = None,
        text: str | None = None,
        edited: bool | None = None,
        sent_at: str | None = None,
    ) -> Message:
        """Change the fields given (None leaves a field as it is) and touch the message's ticket.

        Nothing is filled in for the caller: a message turned "sent" gets a sent_at only if one is
        passed (now_iso() gives the stored format).
        """
        if status is not None:
            _check(status, get_args(MessageStatus), "message status")
        changes = {
            "status": status,
            "text": text,
            "edited": None if edited is None else int(edited),
            "sent_at": sent_at,
        }
        changes = {column: value for column, value in changes.items() if value is not None}
        with self._db.transaction():
            message = self._message(message_id)
            if not changes:
                return message
            assignments = ", ".join(f"{column} = ?" for column in changes)
            self._db.execute(
                f"UPDATE messages SET {assignments} WHERE id = ?", (*changes.values(), message_id)
            )
            self._touch(message.ticket_id, now_iso())
            return self._message(message_id)

    def messages(self, ticket_id: str) -> list[Message]:
        """All of the ticket's messages, drafts and discarded ones included, oldest first."""
        rows = self._db.query(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE ticket_id = ? ORDER BY id", (ticket_id,)
        )
        return [_to_message(row) for row in rows]

    def pending_draft(self, ticket_id: str) -> Message | None:
        """The ticket's newest message whose status is still "draft"."""
        row = self._db.query_one(
            f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE ticket_id = ? AND status = 'draft' "
            "ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        )
        return None if row is None else _to_message(row)

    # Runs

    def create_run(self, *, run_id: str, ticket_id: str, model: str) -> RunRecord:
        """Record a run on the ticket starting now: "running", nothing decided, no steps yet."""
        with self._db.transaction():
            if self.get_ticket(ticket_id) is None:
                raise KeyError(ticket_id)
            self._db.execute(
                "INSERT INTO runs(id, ticket_id, status, model, steps, usage, write_calls, started_at) "
                "VALUES (?, ?, 'running', ?, '[]', '{}', '[]', ?)",
                (run_id, ticket_id, model, now_iso()),
            )
            return self._run(run_id)

    def finish_run(
        self,
        run_id: str,
        *,
        outcome: str,
        decision: str | None,
        steps: list[dict],
        usage: dict,
        write_calls: list[str],
        error: str | None,
    ) -> RunRecord:
        """Mark the run finished now and store how it ended; steps, usage and write_calls as JSON."""
        if decision is not None:
            _check(decision, get_args(RunDecision), "run decision")
        encoded = [json.dumps(value, ensure_ascii=False) for value in (steps, usage, write_calls)]
        with self._db.transaction():
            self._db.execute(
                "UPDATE runs SET status = 'finished', outcome = ?, decision = ?, steps = ?, usage = ?, "
                "write_calls = ?, error = ?, finished_at = ? WHERE id = ?",
                (outcome, decision, *encoded, error, now_iso(), run_id),
            )
            if self._changes() == 0:
                raise KeyError(run_id)
            return self._run(run_id)

    def runs(self, ticket_id: str) -> list[RunRecord]:
        """The ticket's runs in the order they started."""
        rows = self._db.query(
            f"SELECT {_RUN_COLUMNS} FROM runs WHERE ticket_id = ? ORDER BY started_at, rowid",
            (ticket_id,),
        )
        return [_to_run(row) for row in rows]

    # All records

    def clear(self) -> None:
        """Delete every ticket, message and run, all at once (demo reset)."""
        with self._db.transaction():
            for table in ("messages", "runs", "tickets"):
                self._db.execute(f"DELETE FROM {table}")

    # Helpers. The ones that read changes() must run inside a transaction, so that no statement
    # from another thread can come between the change and the count.

    def _next_ticket_id(self) -> str:
        row = self._db.query_one(
            "SELECT MAX(CAST(SUBSTR(id, 3) AS INTEGER)) AS n FROM tickets "
            "WHERE id GLOB 'T-[0-9][0-9][0-9][0-9][0-9][0-9]'"
        )
        highest = row["n"] if row["n"] is not None else 0
        return f"T-{max(highest + 1, _FIRST_TICKET_NUMBER):06d}"

    def _touch(self, ticket_id: str, now: str, status: str | None = None) -> None:
        """Set the ticket's updated_at to now, and its status too if one is given."""
        self._db.execute(
            "UPDATE tickets SET status = COALESCE(?, status), updated_at = ? WHERE id = ?",
            (status, now, ticket_id),
        )
        if self._changes() == 0:
            raise KeyError(ticket_id)

    def _changes(self) -> int:
        """How many rows the last INSERT, UPDATE or DELETE changed."""
        return self._db.query_one("SELECT changes() AS n")["n"]

    def _ticket(self, ticket_id: str) -> Ticket:
        ticket = self.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        return ticket

    def _message(self, message_id: int) -> Message:
        row = self._db.query_one(f"SELECT {_MESSAGE_COLUMNS} FROM messages WHERE id = ?", (message_id,))
        if row is None:
            raise KeyError(message_id)
        return _to_message(row)

    def _run(self, run_id: str) -> RunRecord:
        row = self._db.query_one(f"SELECT {_RUN_COLUMNS} FROM runs WHERE id = ?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        return _to_run(row)


def _check(value: str, allowed: tuple[str, ...], what: str) -> None:
    if value not in allowed:
        raise ValueError(f"unknown {what} {value!r}; expected one of: {', '.join(allowed)}")


def _to_message(row: dict) -> Message:
    return Message(**{**row, "edited": bool(row["edited"])})


def _to_run(row: dict) -> RunRecord:
    return RunRecord(**{**row, **{name: json.loads(row[name]) for name in _RUN_JSON_FIELDS}})
