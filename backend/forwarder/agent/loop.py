"""AgentRunner: the tool-calling loop that answers the latest customer message on a ticket."""

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from forwarder.agent.context import TicketContext, build_messages
from forwarder.kb import KB_TOOL, knowledge_answer, strip_source_lines
from forwarder.llm.base import ChatModel, LLMError, ModelReply, ToolCall
from forwarder.tools.registry import ToolContext, ToolRegistry, ToolResult
from forwarder.world.data import WorldData
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView


@dataclass
class RunStep:
    """One step of a run, handed to emit as soon as it has happened.

    A "model" step is one model call: data = {"content", "tool_calls": [{"id", "name", "arguments"}],
    "usage"}. A "tool" step is one tool call answered: data = {"call_id", "name", "arguments", "kind",
    "observation"}, where kind is the tool's ("read" for a name that is not registered) and
    observation is what the model was sent. data shares nothing the run still uses, so whoever
    receives a step may change it.
    """

    index: int  # position in the run, from 0
    kind: Literal["model", "tool"]
    data: dict


@dataclass
class RunResult:
    """How a run ended, with every step it took.

    outcome "final": the model answered without a tool call; final_text is the answer, stripped.
    "max_steps": every model call allowed asked for tools. "error": the model API failed (error is
    str of the LLMError) or the answer was blank (error "empty_reply"). final_text is set only for
    "final", error only for "error".

    write_calls names the write tools the model called, in call order, one entry per call, refused
    and failed calls included. usage adds up the numbers the model calls reported. model is the
    model name the API last reported, or the model's own name when no reply came.
    """

    run_id: str
    outcome: Literal["final", "max_steps", "error"]
    final_text: str | None
    steps: list[RunStep]
    write_calls: list[str]
    usage: dict
    model: str
    error: str | None


class AgentRunner:
    """Calls the model, answers the tool calls it makes, and repeats until it replies with text.

    One runner serves every run: a run keeps its state to itself, so runs can go on in parallel.
    """

    def __init__(
        self, *, registry: ToolRegistry, ledger: Ledger, world: WorldData, max_steps: int = 10
    ) -> None:
        if max_steps < 1:
            raise ValueError(f"max_steps must be at least 1, not {max_steps}")
        self._registry = registry
        self._ledger = ledger
        self._world = world
        self._max_steps = max_steps  # most model calls one run may make

    def run(
        self,
        *,
        run_id: str,
        ctx: TicketContext,
        model: ChatModel,
        tool_names: list[str],
        company: str,
        emit: Callable[[RunStep], None],
    ) -> RunResult:
        """Answer ctx.latest_message with model, offering it the tools named in tool_names.

        Only the first tool call of a model call runs; writes are staged in the ledger under run_id.
        After max_steps model calls the run stops, once the last call's tool calls are answered.
        An LLMError from the model ends the run with outcome "error"; any other exception, from the
        model, emit or the ledger, propagates.
        """
        messages = build_messages(ctx, company)
        # openai_tools raises KeyError for a name that is not registered; such a name is not offered.
        offered = [name for name in tool_names if self._registry.get(name) is not None]
        tools = self._registry.openai_tools(offered)
        allowed = set(tool_names)
        state = _RunState(run_id, emit, model.name)
        knowledge_hits: list[dict] | None = None
        for _ in range(self._max_steps):
            try:
                reply = model.complete(messages, tools)
            except LLMError as exc:
                return state.result("error", error=str(exc))
            state.model = reply.model
            _add_usage(state.usage, reply.usage)
            messages.append(reply.wire_message)  # as is: it carries reasoning_content back
            state.record("model", _model_step_data(reply))
            if not reply.tool_calls:
                text = strip_source_lines(reply.content)
                if knowledge_hits is not None:
                    text = knowledge_answer(text, knowledge_hits, ctx.reply_language)
                if not text:
                    return state.result("error", error="empty_reply")
                return state.result("final", final_text=text)
            # The API rejects the next request unless every tool call id gets exactly one tool message.
            for position, call in enumerate(reply.tool_calls):
                result = self._answer(call, position, run_id, ctx, allowed)
                observation = result.to_observation()
                if call.name == KB_TOOL and result.ok:
                    knowledge_hits = observation["data"]["hits"]
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(_model_observation(call.name, observation), ensure_ascii=False),
                    }
                )
                state.record(
                    "tool",
                    {
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": copy.deepcopy(call.arguments),
                        "kind": result.kind,
                        "observation": observation,
                    },
                )
                if result.kind == "write":
                    state.write_calls.append(call.name)
        return state.result("max_steps")

    def _answer(
        self, call: ToolCall, position: int, run_id: str, ctx: TicketContext, allowed: set[str]
    ) -> ToolResult:
        """Run the call if it is the first of its model call (position 0) and its arguments parsed.

        Any other call is refused without running.
        """
        if call.arguments_error is not None:
            message = f"参数不是合法的 JSON 对象（{call.arguments_error}），这次调用没有执行，请改正参数后重新调用"
            return self._refuse(call.name, "invalid_json_arguments", message)
        if position > 0:
            message = "每步只能调用一个工具，这次调用没有执行；如果还需要，请在下一步单独调用"
            return self._refuse(call.name, "only_one_tool_per_step", message)
        tool_ctx = ToolContext(
            ticket_id=ctx.ticket_id,
            customer_id=ctx.customer_id,
            namespace=run_id,
            # Built anew for every call, so the run sees the writes it has staged so far.
            view=WorldView(self._world, self._ledger.effects(run_id)),
            ledger=self._ledger,
            latest_message=ctx.latest_message,
        )
        return self._registry.execute(call.name, call.arguments, tool_ctx, allowed)

    def _refuse(self, name: str, error: str, message: str) -> ToolResult:
        """A call answered without running it. Its kind follows the registry: "read" when unknown."""
        spec = self._registry.get(name)
        kind = spec.kind if spec is not None else "read"
        return ToolResult(ok=False, kind=kind, error=error, message=message)


def _model_observation(name: str, observation: dict) -> dict:
    """Give the model verified customs facts and keep the full record in the staff trace."""
    if name in {"quote.calculate", "quote.create", "claim.open", "approval.create_case"} and observation["ok"]:
        data = {key: value for key, value in observation["data"].items() if key != "internal"}
        return {**observation, "data": data}
    if name != "customs.get_progress" or not observation["ok"]:
        return observation

    record = observation["data"]
    steps = [
        {key: value for key, value in step.items() if key != "at"}
        for step in record["steps"]
        if step["status"] == "done"
    ]
    hold = record.get("hold")
    if hold:
        hold = {key: value for key, value in hold.items() if key not in ("since", "resubmitted_at")}
    return {**observation, "data": {**record, "steps": steps, "hold": hold}}


class _RunState:
    """What one run has done so far."""

    def __init__(self, run_id: str, emit: Callable[[RunStep], None], model: str) -> None:
        self.run_id = run_id
        self.emit = emit
        self.model = model
        self.steps: list[RunStep] = []
        self.write_calls: list[str] = []
        self.usage: dict = {}

    def record(self, kind: Literal["model", "tool"], data: dict) -> None:
        step = RunStep(len(self.steps), kind, data)
        self.steps.append(step)
        self.emit(step)

    def result(
        self,
        outcome: Literal["final", "max_steps", "error"],
        *,
        final_text: str | None = None,
        error: str | None = None,
    ) -> RunResult:
        return RunResult(
            run_id=self.run_id,
            outcome=outcome,
            final_text=final_text,
            steps=self.steps,
            write_calls=self.write_calls,
            usage=self.usage,
            model=self.model,
            error=error,
        )


def _model_step_data(reply: ModelReply) -> dict:
    """The model step's data, copied so that changing it cannot change the calls about to run."""
    return {
        "content": reply.content,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": copy.deepcopy(call.arguments)}
            for call in reply.tool_calls
        ],
        "usage": copy.deepcopy(reply.usage),
    }


def _add_usage(total: dict, usage: dict) -> None:
    """Add usage's top-level numbers into total. Nested objects, text and booleans are skipped."""
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[key] = total.get(key, 0) + value
