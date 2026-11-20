import copy
import dataclasses
import json

import pytest

from forwarder.tools.logistics import P1_TOOLS, register_logistics_tools
from forwarder.tools.registry import ToolContext, ToolRegistry
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView


def make_ctx(db, world, customer_id):
    """A run on a ticket of customer_id, over the seed world. Each customer's run has its own namespace."""
    return ToolContext(
        ticket_id=f"T-{customer_id}",
        customer_id=customer_id,
        namespace=f"R-{customer_id}",
        view=WorldView(world, []),
        ledger=Ledger(db),
    )


@pytest.fixture
def reg():
    registry = ToolRegistry()
    register_logistics_tools(registry)
    return registry


@pytest.fixture
def ctx_kr(db, world):
    return make_ctx(db, world, "C-KR-0412")  # FWQD2611043, held for a PCCC mismatch


@pytest.fixture
def ctx_cn(db, world):
    return make_ctx(db, world, "C-CN-2207")  # FWQD2611127, in warehouse job WJ-2611-031


@pytest.fixture
def ctx_cn_individual(db, world):
    return make_ctx(db, world, "C-CN-3150")  # FWQD2611150, held for inspection


@pytest.fixture
def world_with_outbound_done(world, ctx_cn):
    """The seed world with WJ-2611-031 shipped out; ctx_cn is switched over to it."""
    job = copy.deepcopy(world.warehouse_jobs["WJ-2611-031"])
    outbound = next(s for s in job["steps"] if s["key"] == "outbound")
    outbound.update(status="done", at="2026-11-18T16:00:00+09:00")
    shipped = dataclasses.replace(world, warehouse_jobs={**world.warehouse_jobs, job["job_id"]: job})
    ctx_cn.view = WorldView(shipped, [])
    return shipped


def run(reg, ctx, name, **args):
    return reg.execute(name, args, ctx, allowed=set(P1_TOOLS))


def test_customs_progress_shows_pccc_hold(reg, ctx_kr):
    r = run(reg, ctx_kr, "customs.get_progress", hbl="FWQD2611043")
    assert r.ok and r.data["hold"]["code"] == "pccc_mismatch"
    assert [s["key"] for s in r.data["steps"]][:3] == ["arrival_report", "unloading_accepted", "bonded_in"]


def test_shipment_get_rejects_other_customers_hbl(reg, ctx_kr):
    assert run(reg, ctx_kr, "shipment.get", hbl="FWQD2611127").error == "not_found"


def test_customs_and_attachment_ownership(reg, ctx_kr):
    assert run(reg, ctx_kr, "customs.get_progress", hbl="FWQD2611150").error == "not_found"
    assert run(reg, ctx_kr, "attachment.inspect", attachment_id="ATT-9001").error == "not_found"


def test_crm_only_own_customer(reg, ctx_kr):
    assert run(reg, ctx_kr, "crm.get_customer", customer_id="C-KR-0412").data["language"] == "ko"
    assert run(reg, ctx_kr, "crm.get_customer", customer_id="C-CN-2207").error == "not_found"


def test_shipment_list_only_own(reg, ctx_cn):
    hbls = [s["hbl"] for s in run(reg, ctx_cn, "shipment.list", customer_id="C-CN-2207").data["shipments"]]
    assert hbls == ["FWQD2611127"]


def test_submit_pccc_validates_format(reg, ctx_kr):
    assert run(reg, ctx_kr, "customs.submit_pccc", hbl="FWQD2611043", pccc="P12345").error == "invalid_pccc"


def test_submit_pccc_requires_pccc_hold(reg, ctx_cn_individual):
    r = run(reg, ctx_cn_individual, "customs.submit_pccc", hbl="FWQD2611150", pccc="P190722310455")
    assert r.error == "no_pccc_hold"


def test_submit_pccc_stages_masked_value(reg, ctx_kr):
    r = run(reg, ctx_kr, "customs.submit_pccc", hbl="FWQD2611043", pccc="P230518447201")
    assert r.ok and r.kind == "write"
    staged = ctx_kr.ledger.staged(ctx_kr.namespace)[0]
    assert staged.payload == {"hbl": "FWQD2611043", "pccc_masked": "P2305****7201"}
    assert "P230518447201" not in json.dumps(r.to_observation(), ensure_ascii=False)


def test_warehouse_job_and_damage(reg, ctx_cn):
    job = run(reg, ctx_cn, "warehouse.get_job", job_id="WJ-2611-031").data
    insp = next(s for s in job["steps"] if s["key"] == "inspection")
    assert insp["result"] == "damaged" and insp["cartons"] == ["C-014", "C-027", "C-031"]


def test_request_action_rejected_after_outbound(reg, ctx_cn, world_with_outbound_done):
    assert run(reg, ctx_cn, "warehouse.request_action", job_id="WJ-2611-031", action="repack").error == "action_not_allowed"


def test_handoff_is_write(reg, ctx_kr):
    assert run(reg, ctx_kr, "ticket.handoff", reason="客户要求人工").kind == "write"


# Beyond the brief's tests: registration, ownership on every tool, PCCC privacy, the write payloads


def test_registers_the_p1_tools_in_order_with_their_kinds(reg):
    assert [t["function"]["name"] for t in reg.openai_tools(P1_TOOLS)] == P1_TOOLS
    writes = [name for name in P1_TOOLS if reg.get(name).kind == "write"]
    assert writes == ["customs.submit_pccc", "warehouse.request_action", "ticket.handoff"]
    assert "格式为 P 加 12 位数字" in reg.get("customs.submit_pccc").description


# (tool, arguments naming a record another customer owns, the same arguments naming no record at all)
OTHER_CUSTOMERS_RECORDS = [
    ("crm.get_customer", {"customer_id": "C-KR-0412"}, {"customer_id": "C-XX-0000"}),
    ("shipment.list", {"customer_id": "C-KR-0412"}, {"customer_id": "C-XX-0000"}),
    ("shipment.get", {"hbl": "FWQD2611043"}, {"hbl": "FWQD0000000"}),
    ("customs.get_progress", {"hbl": "FWQD2611043"}, {"hbl": "FWQD0000000"}),
    ("warehouse.get_job", {"job_id": "WJ-2611-031"}, {"job_id": "WJ-0000-000"}),
    ("attachment.inspect", {"attachment_id": "ATT-9001"}, {"attachment_id": "ATT-0000"}),
    (
        "customs.submit_pccc",
        {"hbl": "FWQD2611043", "pccc": "P230518447201"},  # does have a PCCC hold
        {"hbl": "FWQD0000000", "pccc": "P230518447201"},
    ),
    (
        "warehouse.request_action",
        {"job_id": "WJ-2611-031", "action": "repack"},
        {"job_id": "WJ-0000-000", "action": "repack"},
    ),
]


@pytest.mark.parametrize(
    "name, theirs, missing", OTHER_CUSTOMERS_RECORDS, ids=[case[0] for case in OTHER_CUSTOMERS_RECORDS]
)
def test_other_customers_records_look_exactly_like_missing_ones(reg, ctx_cn_individual, name, theirs, missing):
    ctx = ctx_cn_individual  # owns none of the records above
    other = run(reg, ctx, name, **theirs)
    assert other.error == "not_found"
    assert other.to_observation() == run(reg, ctx, name, **missing).to_observation()
    assert ctx.ledger.staged(ctx.namespace) == []


def test_owners_see_their_own_records(reg, ctx_kr, ctx_cn):
    assert run(reg, ctx_kr, "shipment.get", hbl="FWQD2611043").data["status"] == "customs_hold"
    assert run(reg, ctx_cn, "attachment.inspect", attachment_id="ATT-9002").data["carton"] == "C-027"
    job = run(reg, ctx_cn, "warehouse.get_job", job_id="WJ-2611-031").data
    # The job's carton count is not called "cartons": that is the inspection's list of carton ids.
    assert job["carton_count"] == 120 and "cartons" not in job


def test_customs_tools_on_own_shipments_without_a_pccc_hold(reg, db, world, ctx_cn):
    # A cleared import has no hold at all; an export has no import clearance record.
    cleared = run(reg, ctx_cn, "customs.submit_pccc", hbl="FWQD2611127", pccc="P230518447201")
    assert cleared.error == "no_pccc_hold"
    exporter = make_ctx(db, world, "C-KR-1088")
    assert run(reg, exporter, "customs.get_progress", hbl="FWICN2611088").error == "not_found"
    exported = run(reg, exporter, "customs.submit_pccc", hbl="FWICN2611088", pccc="P230518447201")
    assert exported.error == "no_pccc_hold"
    assert ctx_cn.ledger.staged(ctx_cn.namespace) == [] and exporter.ledger.staged(exporter.namespace) == []


@pytest.mark.parametrize(
    "pccc",
    [
        "P2305184472019",  # 13 digits
        "p230518447201",  # lower-case p
        "P230518447201\n",  # a bare "$" would let the trailing newline through
        "P２３０５１８４４７２０１",  # full-width digits, which a bare \d accepts
    ],
)
def test_submit_pccc_rejects_near_misses_without_repeating_them(reg, ctx_kr, pccc):
    r = run(reg, ctx_kr, "customs.submit_pccc", hbl="FWQD2611043", pccc=pccc)
    assert r.error == "invalid_pccc" and ctx_kr.ledger.staged(ctx_kr.namespace) == []
    assert pccc.strip() not in json.dumps(r.to_observation(), ensure_ascii=False)


def test_warehouse_requests_and_handoffs_stage_their_payloads(reg, ctx_cn):
    repack = run(
        reg, ctx_cn, "warehouse.request_action",
        job_id="WJ-2611-031", action="repack", cartons=["C-014", "C-027"], note="换新外箱",
    )
    assert repack.ok and repack.kind == "write"
    assert run(reg, ctx_cn, "warehouse.request_action", job_id="WJ-2611-031", action="hold").ok
    assert run(reg, ctx_cn, "ticket.handoff", reason="客户要求人工").ok
    repack_payload = {"job_id": "WJ-2611-031", "action": "repack", "cartons": ["C-014", "C-027"], "note": "换新外箱"}
    assert [(r.tool, r.payload) for r in ctx_cn.ledger.staged(ctx_cn.namespace)] == [
        ("warehouse.request_action", repack_payload),
        ("warehouse.request_action", {"job_id": "WJ-2611-031", "action": "hold", "cartons": [], "note": None}),
        ("ticket.handoff", {"reason": "客户要求人工"}),
    ]
    unknown = run(reg, ctx_cn, "warehouse.request_action", job_id="WJ-2611-031", action="discard")
    assert unknown.error == "invalid_arguments"


def test_a_runs_staged_writes_show_once_its_view_includes_them(reg, world, ctx_kr, ctx_cn):
    # As in the agent loop, where every tool call gets a view with the run's own staged writes.
    run(reg, ctx_kr, "customs.submit_pccc", hbl="FWQD2611043", pccc="P230518447201")
    ctx_kr.view = WorldView(world, ctx_kr.ledger.effects(ctx_kr.namespace))
    progress = run(reg, ctx_kr, "customs.get_progress", hbl="FWQD2611043").data
    assert progress["hold"]["status"] == "resubmitted" and progress["steps"][-1]["key"] == "pccc_resubmitted"
    run(reg, ctx_cn, "warehouse.request_action", job_id="WJ-2611-031", action="repack", cartons=["C-014"])
    ctx_cn.view = WorldView(world, ctx_cn.ledger.effects(ctx_cn.namespace))
    [request] = run(reg, ctx_cn, "warehouse.get_job", job_id="WJ-2611-031").data["requested_actions"]
    assert (request["action"], request["cartons"], request["pending"]) == ("repack", ["C-014"], True)
