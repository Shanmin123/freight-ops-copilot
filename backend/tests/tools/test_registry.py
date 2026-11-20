import copy

import pytest
from jsonschema.exceptions import SchemaError

from forwarder.tools.registry import ToolContext, ToolError, ToolRegistry, ToolResult, ToolSpec
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView

READ_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "string", "description": "记录编号"}},
    "required": ["id"],
    "additionalProperties": False,
}
WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "记录编号"},
        "cartons": {"type": "array", "items": {"type": "string"}, "description": "箱号"},
    },
    "required": ["id"],
    "additionalProperties": False,
}


def read_record(arguments, ctx):
    if arguments["id"] == "missing":
        raise ToolError("not_found")
    if arguments["id"] == "broken":
        raise RuntimeError("disk on fire")
    return {"id": arguments["id"], "customer_id": ctx.customer_id}


def write_record(arguments, ctx):
    if arguments["id"] == "locked":
        raise ToolError("record_locked", "该记录已锁定")
    return {"id": arguments["id"], "cartons": arguments.get("cartons", [])}


@pytest.fixture
def reg():
    registry = ToolRegistry()
    # Fresh schema copies, so a test that edits one cannot leak into the next.
    registry.register(ToolSpec("demo.read", "读取演示记录", copy.deepcopy(READ_SCHEMA), "read", read_record))
    registry.register(ToolSpec("demo.write", "修改演示记录", copy.deepcopy(WRITE_SCHEMA), "write", write_record))
    return registry


@pytest.fixture
def ctx(db, world):
    return ToolContext(
        ticket_id="T-test",
        customer_id="C-KR-0412",
        namespace="R-test",
        view=WorldView(world, []),
        ledger=Ledger(db),
    )


def test_unknown_tool(reg, ctx):
    assert reg.execute("nope.x", {}, ctx, allowed={"nope.x"}).error == "unknown_tool"


def test_not_in_profile(reg, ctx):
    assert reg.execute("demo.read", {"id": "1"}, ctx, allowed=set()).error == "tool_not_allowed"


def test_missing_required_argument(reg, ctx):
    r = reg.execute("demo.read", {}, ctx, allowed={"demo.read"})
    assert r.ok is False and r.error == "invalid_arguments" and "id" in r.message


def test_tool_error_code_propagates(reg, ctx):
    assert reg.execute("demo.read", {"id": "missing"}, ctx, allowed={"demo.read"}).error == "not_found"


def test_write_tool_is_staged(reg, ctx):
    r = reg.execute("demo.write", {"id": "1"}, ctx, allowed={"demo.write"})
    assert r.ok and r.kind == "write" and r.staged_record_id
    assert [x.tool for x in ctx.ledger.staged(ctx.namespace)] == ["demo.write"]
    assert r.to_observation()["note"] == "该操作将在员工确认回复后生效"


def test_openai_tools_shape(reg):
    t = reg.openai_tools(["demo.read"])[0]
    assert t["type"] == "function" and t["function"]["name"] == "demo.read"
    assert t["function"]["parameters"]["required"] == ["id"]


# Registration (ruling R9) and the rest of the execution contract


@pytest.mark.parametrize("name", ["demo__read", "demo_.read", "demo.__read"])
def test_register_rejects_a_name_the_wire_format_would_change(reg, name):
    with pytest.raises(ValueError):
        reg.register(ToolSpec(name, "x", copy.deepcopy(READ_SCHEMA), "read", read_record))
    assert reg.get(name) is None


def test_register_rejects_a_duplicate_name(reg):
    first = reg.get("demo.read")
    with pytest.raises(ValueError):
        reg.register(ToolSpec("demo.read", "again", copy.deepcopy(READ_SCHEMA), "read", read_record))
    assert reg.get("demo.read") is first


def test_register_rejects_a_bad_kind_or_schema(reg):
    with pytest.raises(ValueError):
        reg.register(ToolSpec("demo.other", "x", copy.deepcopy(READ_SCHEMA), "delete", read_record))
    with pytest.raises(SchemaError):
        reg.register(ToolSpec("demo.other", "x", {"type": "object", "required": "id"}, "read", read_record))
    assert reg.get("demo.other") is None


def test_read_tool_returns_the_handler_data_and_stages_nothing(reg, ctx):
    r = reg.execute("demo.read", {"id": "7"}, ctx, allowed={"demo.read"})
    assert r == ToolResult(ok=True, kind="read", data={"id": "7", "customer_id": "C-KR-0412"})
    assert r.to_observation() == {"ok": True, "data": {"id": "7", "customer_id": "C-KR-0412"}}
    assert ctx.ledger.staged(ctx.namespace) == []


def test_write_tool_stages_its_payload_under_the_run_and_ticket(reg, ctx):
    r = reg.execute("demo.write", {"id": "1", "cartons": ["C-014"]}, ctx, allowed={"demo.write"})
    [record] = ctx.ledger.staged("R-test")
    assert (record.id, record.ticket_id, record.tool, record.payload) == (
        r.staged_record_id, "T-test", "demo.write", {"id": "1", "cartons": ["C-014"]}
    )
    assert r.to_observation() == {
        "ok": True, "data": {"id": "1", "cartons": ["C-014"]}, "note": "该操作将在员工确认回复后生效"
    }


def test_failures_carry_the_handlers_code_and_message(reg, ctx):
    r = reg.execute("demo.write", {"id": "locked"}, ctx, allowed={"demo.write"})
    assert r == ToolResult(ok=False, kind="write", error="record_locked", message="该记录已锁定")
    assert r.to_observation() == {"ok": False, "error": "record_locked", "message": "该记录已锁定"}
    assert ctx.ledger.staged(ctx.namespace) == []  # a failed write stages nothing and gets no note
    missing = reg.execute("demo.read", {"id": "missing"}, ctx, allowed={"demo.read"})
    assert missing.to_observation() == {"ok": False, "error": "not_found", "message": None}


def test_checks_run_in_order_and_stop_at_the_first_failure(reg, ctx):
    unknown = reg.execute("nope.x", {}, ctx, allowed=set())
    assert (unknown.ok, unknown.kind, unknown.error) == (False, "read", "unknown_tool")
    # Not allowed is reported before the arguments are looked at, and the handler never runs.
    assert reg.execute("demo.read", {}, ctx, allowed=set()).error == "tool_not_allowed"
    blocked = reg.execute("demo.write", {"id": "1"}, ctx, allowed={"demo.read"})
    assert (blocked.ok, blocked.kind, blocked.error) == (False, "write", "tool_not_allowed")
    assert ctx.ledger.staged(ctx.namespace) == []


def test_invalid_arguments_name_every_offending_field(reg, ctx):
    r = reg.execute("demo.write", {"cartons": ["C-014", 7], "extra": 1}, ctx, allowed={"demo.write"})
    assert (r.ok, r.kind, r.error) == (False, "write", "invalid_arguments")
    # The wrong item by its path, the missing argument, and the unexpected one.
    assert "cartons[1]" in r.message and "'id'" in r.message and "'extra'" in r.message
    assert ctx.ledger.staged(ctx.namespace) == []


def test_handler_crash_becomes_internal_error_and_is_logged(reg, ctx, caplog):
    r = reg.execute("demo.read", {"id": "broken"}, ctx, allowed={"demo.read"})
    assert (r.ok, r.kind, r.error) == (False, "read", "internal_error")
    # The model sees only the exception type; the details go to the log with the traceback.
    assert "RuntimeError" in r.message and "disk on fire" not in r.message
    assert "disk on fire" in caplog.text


def test_tool_error_text_shows_code_and_message():
    assert str(ToolError("not_found")) == "not_found"
    assert str(ToolError("record_locked", "该记录已锁定")) == "record_locked: 该记录已锁定"


def test_openai_tools_follow_the_given_names_and_copy_schemas(reg):
    tools = reg.openai_tools(["demo.write", "demo.read"])
    assert [t["function"]["name"] for t in tools] == ["demo.write", "demo.read"]
    assert tools[1] == {
        "type": "function",
        "function": {"name": "demo.read", "description": "读取演示记录", "parameters": READ_SCHEMA},
    }
    tools[1]["function"]["parameters"]["required"].append("x")
    assert reg.get("demo.read").parameters["required"] == ["id"]
    with pytest.raises(KeyError):
        reg.openai_tools(["nope.x"])
