"""Tool framework: tool specs, and the registry that validates and runs calls and stages writes."""

import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, get_args

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from forwarder.llm.base import from_wire_name, to_wire_name
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView

logger = logging.getLogger(__name__)

ToolKind = Literal["read", "write"]

# Added to the observation of every write that went through: the write is only staged, so the model
# must not tell the customer it is done.
STAGED_NOTE = "该操作将在员工确认回复后生效"


class ToolError(Exception):
    """Raised by a handler to fail the call with a code the model can act on, e.g. "not_found"."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(code if message is None else f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass
class ToolContext:
    """What a handler works with. Plain (not frozen), so a caller can swap in another view."""

    ticket_id: str
    customer_id: str  # the ticket's customer
    namespace: str  # the run's ledger namespace, where its writes are staged
    view: WorldView  # the world as this run sees it, its own staged writes included
    ledger: Ledger
    latest_message: str = ""


@dataclass(frozen=True)
class ToolSpec:
    """One tool: what the model is told about it, and the handler that runs it.

    A read handler returns the result data. A write handler returns the payload to stage; the
    registry stages it. Either raises ToolError for a failure the model should see.
    """

    name: str  # dotted, e.g. "customs.get_progress"
    description: str  # tells the model when to use the tool
    parameters: dict  # JSON Schema of the arguments object
    kind: ToolKind
    handler: Callable[[dict, ToolContext], dict]


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool call. to_observation() is what goes back to the model."""

    ok: bool
    kind: str  # the tool's kind, "read" or "write"
    data: dict | None = None  # read: the handler's result; write: the payload as staged
    error: str | None = None  # error code, set when not ok
    message: str | None = None  # what went wrong, when there is more to say than the code
    staged_record_id: int | None = None  # ledger record id of a staged write

    def to_observation(self) -> dict:
        if not self.ok:
            return {"ok": False, "error": self.error, "message": self.message}
        observation = {"ok": True, "data": self.data}
        if self.kind == "write":
            observation["note"] = STAGED_NOTE
        return observation


class ToolRegistry:
    """The registered tools by dotted name; validates and runs calls against them."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        """Add a tool, refusing a spec that could not work.

        ValueError: the name is taken; or it would not survive the wire format, i.e.
        from_wire_name(to_wire_name(name)) != name, as with "__" or "_." in it; or the kind is not
        "read" or "write". jsonschema's SchemaError: parameters is not a valid JSON Schema.
        """
        name = spec.name
        returned = from_wire_name(to_wire_name(name))
        if returned != name:
            raise ValueError(f"tool name {name!r} would come back from the model as {returned!r}")
        if name in self._specs:
            raise ValueError(f"a tool named {name!r} is already registered")
        if spec.kind not in get_args(ToolKind):
            raise ValueError(f"tool {name!r}: kind must be 'read' or 'write', not {spec.kind!r}")
        Draft202012Validator.check_schema(spec.parameters)
        self._specs[name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def openai_tools(self, names: list[str]) -> list[dict]:
        """OpenAI-format function specs of the named tools, in the order given.

        Names stay dotted; the model client converts them for the wire. KeyError for a name that is
        not registered.
        """
        return [_openai_tool(self._specs[name]) for name in names]

    def execute(self, name: str, arguments: dict, ctx: ToolContext, allowed: set[str]) -> ToolResult:
        """Run one tool call. A failed call never raises; it comes back with an error code.

        Checked in this order, stopping at the first failure: the tool exists (unknown_tool), it
        is in allowed (tool_not_allowed), the arguments match its schema (invalid_arguments, naming
        each offending field). Then the handler runs: a ToolError gives its own code and message,
        any other exception gives internal_error. A write's payload is staged in ctx.namespace for
        ctx.ticket_id and returned as data, with the ledger record's id.
        """
        spec = self._specs.get(name)
        if spec is None:
            # No tool ran, so nothing was written: an unknown name counts as a read.
            return _failure("read", "unknown_tool", f"没有名为 {name} 的工具")
        if name not in allowed:
            return _failure(spec.kind, "tool_not_allowed", f"工具 {name} 不在本次可用的工具里")
        try:
            validator = Draft202012Validator(spec.parameters)
            problems = [_describe(error) for error in validator.iter_errors(arguments)]
            if problems:
                message = "参数不符合要求：" + "；".join(problems)
                return _failure(spec.kind, "invalid_arguments", message)
            data = spec.handler(arguments, ctx)
            if spec.kind == "read":
                return ToolResult(ok=True, kind="read", data=data)
            record = ctx.ledger.stage(
                namespace=ctx.namespace, ticket_id=ctx.ticket_id, tool=name, payload=data
            )
            return ToolResult(ok=True, kind="write", data=record.payload, staged_record_id=record.id)
        except ToolError as exc:
            return _failure(spec.kind, exc.code, exc.message)
        except Exception as exc:
            logger.exception("tool %s failed in run %s", name, ctx.namespace)
            message = f"工具执行时出现内部错误（{type(exc).__name__}）"
            return _failure(spec.kind, "internal_error", message)


def _failure(kind: str, error: str, message: str | None) -> ToolResult:
    return ToolResult(ok=False, kind=kind, error=error, message=message)


def _describe(error: ValidationError) -> str:
    """One schema violation, led by its argument: "cartons[1]: 7 is not of type 'string'".

    A missing or unexpected argument is reported on the arguments object itself, and jsonschema's
    message already names it ("'id' is a required property"), so it gets no prefix.
    """
    where = error.json_path.removeprefix("$").removeprefix(".")
    return f"{where}: {error.message}" if where else error.message


def _openai_tool(spec: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            # A copy: editing a returned tool list must not change the schema calls are checked with.
            "parameters": copy.deepcopy(spec.parameters),
        },
    }
