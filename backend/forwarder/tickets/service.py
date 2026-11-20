"""TicketService: the ticket workflow, from a customer message to the reply the customer gets.

Every customer message starts an agent run on the customer's ticket. The run's reply goes out at once
when the run only read data and auto-reply is on; otherwise it waits as a draft, with the run's writes
staged, until staff confirm it (the writes are committed) or send it back for a rewrite (discarded).
"""

import copy
import dataclasses
import functools
import json
import logging
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from forwarder.agent.context import TicketContext
from forwarder.agent.loop import AgentRunner, RunResult, RunStep
from forwarder.config import Settings
from forwarder.llm.base import ChatModel
from forwarder.privacy import mask_pccc, mask_pccc_deep
from forwarder.world.data import WorldData
from forwarder.world.ledger import Ledger

from .events import EventBus
from .executor import Executor
from .models import Message, Ticket, now_iso
from .repo import TicketRepo

logger = logging.getLogger(__name__)

# In the customer's reply language. A cycle is the stretch from one customer message to the next;
# the first draft of a cycle is preceded by the ack, so the customer knows a reply is on its way.
ACK_TEXTS = {
    "zh": "已收到您的消息，我们正在核对，稍后回复您。",
    "ko": "메시지를 확인했습니다. 확인 후 곧 답변드리겠습니다.",
}
# The draft of a run that used up its model calls without replying.
MAX_STEPS_TEXTS = {
    "zh": "这个问题需要同事进一步确认，我们会尽快回复您。",
    "ko": "담당자가 추가로 확인한 후 빠르게 답변드리겠습니다.",
}


class NotADraftError(ValueError):
    """The message is not a pending draft: it was sent or discarded, or never was a draft."""


class TicketService:
    """The workflow's rules, kept by one lock that every change of state happens under.

    Only the agent run itself (model and tool calls) goes on outside the lock, on the executor. A
    ticket has at most one run queued or under way, and never while it has a pending draft.

    Agent messages and run steps are stored and published with any PCCC masked. Customer messages
    are kept as written: a later run (a rerun or a rewrite) needs the real number to submit it.
    """

    def __init__(
        self,
        *,
        repo: TicketRepo,
        ledger: Ledger,
        runner: AgentRunner,
        bus: EventBus,
        world: WorldData,
        settings: Settings,
        model_factory: Callable[[], ChatModel],
        executor: Executor,
        tool_names: list[str],
    ) -> None:
        self._repo = repo
        self._ledger = ledger
        self._runner = runner
        self._bus = bus
        self._world = world
        self._settings = settings  # read when needed: auto_reply_readonly can change at any time
        self._model_factory = model_factory  # called once per run
        self._executor = executor
        self._tool_names = list(tool_names)
        self._lock = threading.Lock()
        self._running: set[str] = set()  # tickets with a run queued or under way
        self._rerun: set[str] = set()  # of those, the ones written to since their run read the ticket
        self._force_draft: set[str] = set()
        self._epoch = 0  # bumped by reset_demo: a run submitted before then leaves no trace
        self.on_confirmed: Callable[[Message], None] | None = None

    def customer_message(
        self, *, customer_id: str, channel: str, text: str, new_ticket: bool = False,
        subject: str | None = None, force_draft: bool = False,
    ) -> tuple[Ticket, Message]:
        """Add the message to the customer's latest ticket on channel, or to a new one, and answer it.

        A new ticket's subject is the first 30 characters of the message, with any PCCC masked. A
        pending draft is discarded with its staged writes, then a run starts; but if a run is under
        way, it is only marked stale: once it ends, its result is dropped and a new run starts.
        KeyError for an unknown customer, ValueError for an unknown channel. Returns the ticket as
        it is now, in processing, and the message.
        """
        if customer_id not in self._world.customers:
            raise KeyError(customer_id)
        with self._lock:
            ticket = None if new_ticket else self._repo.latest_ticket_for(customer_id, channel)
            if ticket is None:
                title = mask_pccc(subject or text)[:(160 if subject else 30)]
                ticket = self._repo.create_ticket(customer_id=customer_id, channel=channel, subject=title)
            if force_draft:
                self._force_draft.add(ticket.id)
            message = self._repo.add_message(ticket_id=ticket.id, role="customer", text=text)
            self._publish_message("message.created", message)
            draft = self._repo.pending_draft(ticket.id)
            if draft is not None:
                self._discard(draft)
            ticket = self._set_status(ticket.id, "processing")
            run = self._claim(ticket.id)
        if run is not None:
            self._executor.submit(run)
        return ticket, message

    def confirm_draft(self, ticket_id: str, message_id: int, *, text: str | None = None) -> Message:
        """Commit the draft's staged writes and send it, as text if given (it is then marked edited).

        The ticket becomes needs_human if the draft is the holding text of a run that ran out of
        steps, or if the run's committed writes hand the ticket off; replied otherwise. KeyError if
        the ticket has no such message, NotADraftError if it is not a pending draft.
        """
        with self._lock:
            draft = self._draft(ticket_id, message_id)
            self._ledger.commit(draft.run_id)
            edits = {} if text is None else {"text": mask_pccc(text), "edited": True}
            sent = self._repo.update_message(draft.id, status="sent", sent_at=now_iso(), **edits)
            self._publish_message("message.updated", sent)
            # The holding text promises that a colleague follows up, however staff worded it.
            held = any(
                run.id == draft.run_id and run.outcome == "max_steps" for run in self._repo.runs(ticket_id)
            )
            # Read from the ledger, not from the run's write calls: a failed handoff hands nothing off.
            handed_off = any(
                record.namespace == draft.run_id and record.tool == "ticket.handoff"
                for record in self._ledger.effects()
            )
            self._set_status(ticket_id, "needs_human" if held or handed_off else "replied")
        if self.on_confirmed is not None:
            try:
                self.on_confirmed(sent)
            except Exception:
                logger.exception("could not notify mail automation after confirming %s", ticket_id)
        return sent

    def regenerate_draft(self, ticket_id: str, message_id: int) -> None:
        """Discard the draft and its staged writes, and start a new run; the cycle goes on, so no ack.

        Staff asked for the rewrite, so it always waits for them as a draft, never sent at once,
        whatever auto-reply says. KeyError if the ticket has no such message, NotADraftError if it
        is not a pending draft.
        """
        with self._lock:
            self._discard(self._draft(ticket_id, message_id))
            self._set_status(ticket_id, "processing")
            run = self._claim(ticket_id, rewrite=True)
        if run is not None:
            self._executor.submit(run)

    def ticket_detail(self, ticket_id: str) -> dict:
        """{"ticket", "customer", "messages", "runs"} as plain data; messages include drafts and
        discarded ones. KeyError for an unknown ticket."""
        ticket = self._repo.get_ticket(ticket_id)
        if ticket is None:
            raise KeyError(ticket_id)
        return {
            "ticket": ticket.to_dict(),
            "customer": copy.deepcopy(self._world.customers.get(ticket.customer_id)),
            "messages": [message.to_dict() for message in self._repo.messages(ticket_id)],
            "runs": [run.to_dict() for run in self._repo.runs(ticket_id)],
        }

    def customer_messages(self, ticket_id: str) -> list[Message]:
        """The ticket's messages the customer can see: the sent ones, oldest first."""
        return [message for message in self._repo.messages(ticket_id) if message.visible_to_customer]

    def reset_demo(self, seed_tickets_path: Path) -> None:
        """Delete every ticket, message, run and ledger record, load the seed tickets, then publish
        demo.reset (no ticket, empty payload), on which clients reload.

        The seed file is a JSON list of {"id", "customer_id", "channel", "subject", "status",
        "messages": [{"role", "text"}]}; each seed message is a sent, normal one. A run submitted
        before the reset changes nothing afterwards, and whatever it stages is discarded.
        """
        seeds = json.loads(Path(seed_tickets_path).read_text(encoding="utf-8"))
        with self._lock:
            self._epoch += 1
            self._running.clear()
            self._rerun.clear()
            self._force_draft.clear()
            self._repo.clear()
            self._ledger.clear()
            for seed in seeds:
                ticket = self._repo.create_ticket(
                    customer_id=seed["customer_id"], channel=seed["channel"], subject=seed["subject"],
                    ticket_id=seed["id"],
                )
                for message in seed["messages"]:
                    self._repo.add_message(ticket_id=ticket.id, role=message["role"], text=message["text"])
                self._repo.set_status(ticket.id, seed["status"])
            self._bus.publish("demo.reset", None, {})

    # The run, on the executor

    def _run(self, ticket_id: str, epoch: int, rewrite: bool = False) -> None:
        """One agent run on the ticket, then whatever its outcome calls for. A rewrite never auto-sends."""
        try:
            model = self._model_factory()
        except Exception:  # with no model no run can start, but the ticket must not stay processing
            logger.exception("could not create the model for a run on ticket %s", ticket_id)
            self._give_up(ticket_id, epoch)
            return
        run_id = f"R-{uuid.uuid4().hex[:12]}"
        steps: list[dict] = []  # as stored and published: plain data with any PCCC masked
        try:
            with self._lock:
                if epoch != self._epoch:
                    return
                self._rerun.discard(ticket_id)  # from now on, a customer message makes this run stale
                self._repo.create_run(run_id=run_id, ticket_id=ticket_id, model=model.name)
                context = self._context(ticket_id)
                self._bus.publish("run.started", ticket_id, {"run_id": run_id, "model": model.name})
                self._bus.publish("ticket.typing", ticket_id, {"typing": True})
            result = self._runner.run(
                run_id=run_id,
                ctx=context,
                model=model,
                tool_names=self._tool_names,
                company=self._settings.company_display_name,
                emit=lambda step: self._record_step(ticket_id, run_id, epoch, step, steps),
            )
        except Exception as exc:  # the loop makes an outcome of LLMError only; a bug ends the run too
            logger.exception("run %s on ticket %s failed", run_id, ticket_id)
            result = RunResult(
                run_id=run_id, outcome="error", final_text=None, steps=[], write_calls=[], usage={},
                model=model.name, error=f"{type(exc).__name__}: {exc}",
            )
        rerun = self._finish(ticket_id, epoch, rewrite, result, steps)
        if rerun is not None:
            self._executor.submit(rerun)

    def _context(self, ticket_id: str) -> TicketContext:
        """The ticket as the run sees it: sent, normal messages only, so no acks, drafts or discarded
        ones. The latest customer message is the one to answer; what came before it is the transcript."""
        ticket = self._repo.get_ticket(ticket_id)
        customer = self._world.customers[ticket.customer_id]
        said = [m for m in self._repo.messages(ticket_id) if m.status == "sent" and m.kind == "normal"]
        latest = max(i for i, message in enumerate(said) if message.role == "customer")
        return TicketContext(
            ticket_id=ticket.id,
            channel=ticket.channel,
            customer_id=ticket.customer_id,
            customer_name=customer["name"],
            transcript=[(message.role, message.text) for message in said[:latest]],
            latest_message=said[latest].text,
            customer_language="ko" if customer.get("language") == "ko" else "zh",
        )

    def _record_step(self, ticket_id: str, run_id: str, epoch: int, step: RunStep, steps: list[dict]) -> None:
        """Keep the step for the run record and publish it, as plain data with any PCCC masked."""
        data = mask_pccc_deep(dataclasses.asdict(step))
        with self._lock:
            if epoch == self._epoch:
                steps.append(data)
                self._bus.publish("run.step", ticket_id, {"run_id": run_id, "step": data})

    def _finish(
        self, ticket_id: str, epoch: int, rewrite: bool, result: RunResult, steps: list[dict]
    ) -> Callable[[], None] | None:
        """Store the run and act on its outcome. Returns the rerun to submit if the run went stale."""
        with self._lock:
            if epoch != self._epoch:  # the demo was reset during the run
                self._ledger.discard(result.run_id)
                return None
            stale = ticket_id in self._rerun
            if stale or result.outcome != "final":
                # A stale result is dropped, and a run that never got to a reply (an error, or out of
                # steps) must not have its writes applied by confirming a placeholder draft.
                self._ledger.discard(result.run_id)
            # A rewrite decides as if auto-reply were off: staff asked for it, so staff review it.
            auto_reply = self._settings.auto_reply_readonly and not rewrite and ticket_id not in self._force_draft
            decision = "none" if stale else _decide(result, auto_reply)
            self._repo.finish_run(
                result.run_id, outcome=result.outcome, decision=decision, steps=steps,
                usage=result.usage, write_calls=result.write_calls, error=result.error,
            )
            if not stale:
                self._running.discard(ticket_id)
                self._deliver(ticket_id, decision, result)
            self._bus.publish("ticket.typing", ticket_id, {"typing": False})
            self._bus.publish(
                "run.finished",
                ticket_id,
                {
                    "run_id": result.run_id, "outcome": result.outcome, "decision": decision,
                    "error": result.error, "usage": result.usage,
                },
            )
            # The rerun answers the customer message that made this run stale, so it is no rewrite.
            return functools.partial(self._run, ticket_id, epoch) if stale else None

    def _deliver(self, ticket_id: str, decision: str, result: RunResult) -> None:
        """The messages and ticket status that the run's decision calls for."""
        if decision == "none":  # an error: nothing goes to the customer
            self._set_status(ticket_id, "needs_human")
        elif decision == "auto_sent":
            self._add_agent_message(ticket_id, result.final_text, "sent", result.run_id)
            self._set_status(ticket_id, "ai_replied")
        else:
            language = self._context(ticket_id).reply_language
            if self._ack_due(ticket_id):
                self._add_agent_message(ticket_id, ACK_TEXTS[language], "sent", result.run_id, kind="ack")
            text = result.final_text if result.outcome == "final" else MAX_STEPS_TEXTS[language]
            self._add_agent_message(ticket_id, text, "draft", result.run_id)
            self._set_status(ticket_id, "awaiting_confirmation")

    def _give_up(self, ticket_id: str, epoch: int) -> None:
        """A run that could not start hands the ticket to staff, so it does not stay processing."""
        with self._lock:
            if epoch == self._epoch:
                self._running.discard(ticket_id)
                self._rerun.discard(ticket_id)
                self._set_status(ticket_id, "needs_human")

    # Helpers that run under the lock

    def _claim(self, ticket_id: str, rewrite: bool = False) -> Callable[[], None] | None:
        """The run to submit for the ticket once the lock is released; None if a run is queued or
        under way already, which is then marked stale instead."""
        if ticket_id in self._running:
            self._rerun.add(ticket_id)
            return None
        self._running.add(ticket_id)
        return functools.partial(self._run, ticket_id, self._epoch, rewrite)

    def _draft(self, ticket_id: str, message_id: int) -> Message:
        """The ticket's message message_id, which has to be a pending draft."""
        message = next((m for m in self._repo.messages(ticket_id) if m.id == message_id), None)
        if message is None:
            raise KeyError(message_id)
        if message.status != "draft":
            raise NotADraftError(f"message {message_id} is {message.status}, not a draft")
        return message

    def _discard(self, draft: Message) -> None:
        """Discard a pending draft and the writes its run staged."""
        self._ledger.discard(draft.run_id)
        self._publish_message("message.updated", self._repo.update_message(draft.id, status="discarded"))

    def _ack_due(self, ticket_id: str) -> bool:
        """Whether the current cycle, the messages since the latest customer message, has no ack yet."""
        for message in reversed(self._repo.messages(ticket_id)):
            if message.kind == "ack":
                return False
            if message.role == "customer":
                return True
        return True

    def _add_agent_message(
        self, ticket_id: str, text: str, status: str, run_id: str, kind: str = "normal"
    ) -> None:
        """Add an agent message, its text with any PCCC masked, and publish it."""
        message = self._repo.add_message(
            ticket_id=ticket_id, role="agent", text=mask_pccc(text), kind=kind, status=status, run_id=run_id
        )
        self._publish_message("message.created", message)

    def _set_status(self, ticket_id: str, status: str) -> Ticket:
        """Set the ticket's status and publish the ticket as it now is."""
        ticket = self._repo.set_status(ticket_id, status)
        self._bus.publish("ticket.updated", ticket_id, {"ticket": ticket.to_dict()})
        return ticket

    def _publish_message(self, event_type: str, message: Message) -> None:
        self._bus.publish(event_type, message.ticket_id, {"message": message.to_dict()})


def _decide(result: RunResult, auto_reply: bool) -> str:
    """The decision for a run that is not stale: "auto_sent" for a reply that only read data while
    auto-reply is on, "none" for an error, "draft" for everything else (writes, or out of steps)."""
    if result.outcome == "error":
        return "none"
    quoted = any(
        step.kind == "tool" and step.data["name"] == "quote.calculate" and step.data["observation"]["ok"]
        for step in result.steps
    )
    if result.outcome == "final" and not result.write_calls and not quoted and auto_reply:
        return "auto_sent"
    return "draft"
