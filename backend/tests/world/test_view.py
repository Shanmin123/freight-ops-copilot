import copy
import dataclasses
import json
import re
from datetime import datetime, timedelta

import pytest

from forwarder.world.data import load_world
from forwarder.world.ledger import Ledger
from forwarder.world.view import WorldView

CUSTOMS_STEP_KEYS = [
    "arrival_report", "unloading_accepted", "bonded_in", "import_declared", "import_accepted", "bonded_out"
]
PCCC_PAYLOAD = {"hbl": "FWQD2611043", "pccc_masked": "P2305****7201"}


def pick(record, *keys):
    return tuple(record[key] for key in keys)


def strings_in(value):
    """Every string anywhere inside value."""
    if isinstance(value, dict):
        for item in value.values():
            yield from strings_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings_in(item)
    elif isinstance(value, str):
        yield value


def test_customs_overlay_marks_pccc_resubmitted(world, db):
    led = Ledger(db)
    led.stage(namespace="R-1", ticket_id="T-1", tool="customs.submit_pccc", payload={"hbl": "FWQD2611043", "pccc_masked": "P2305****7201"})
    c = WorldView(world, led.effects("R-1")).customs("FWQD2611043")
    assert c["hold"]["status"] == "resubmitted"
    assert c["steps"][-1]["key"] == "pccc_resubmitted"
    assert WorldView(world, led.effects()).customs("FWQD2611043")["hold"]["status"] == "open"


def test_warehouse_overlay_lists_requested_actions(world, db):
    led = Ledger(db); led.stage(namespace="R-1", ticket_id="T-1", tool="warehouse.request_action", payload={"job_id": "WJ-2611-031", "action": "repack", "cartons": ["C-014"]})
    led.commit("R-1")
    job = WorldView(world, led.effects()).warehouse_job("WJ-2611-031")
    assert job["requested_actions"][0]["action"] == "repack"


def test_view_returns_copies(world):
    v = WorldView(world, []); v.shipment("FWQD2611043")["status"] = "x"
    assert WorldView(world, []).shipment("FWQD2611043")["status"] == "customs_hold"


# Seed data (data.py)


def test_seed_tables_are_indexed_by_their_keys(world):
    assert set(world.customers) == {"C-KR-0412", "C-CN-2207", "C-KR-1088", "C-CN-3150"}
    assert set(world.shipments) == {"FWQD2611043", "FWQD2611150", "FWICN2611088", "FWQD2611127"}
    assert set(world.customs) == {"FWQD2611043", "FWQD2611150", "FWQD2611127"}
    assert set(world.warehouse_jobs) == {"WJ-2611-031"}
    assert set(world.attachments) == {"ATT-9001", "ATT-9002", "ATT-9003"}
    tables = [
        (world.customers, "id"),
        (world.shipments, "hbl"),
        (world.customs, "hbl"),
        (world.warehouse_jobs, "job_id"),
        (world.attachments, "attachment_id"),
    ]
    for table, key in tables:
        assert all(record[key] == record_key for record_key, record in table.items())


def test_seed_customers(world):
    c = world.customers
    keys = ("name", "type", "language", "settlement", "tier", "credit_limit_usd", "credit_used_usd")
    assert pick(c["C-KR-0412"], *keys) == ("김지우", "individual", "ko", "prepaid", None, None, None)
    assert pick(c["C-CN-2207"], *keys) == ("云帆优品", "seller", "zh", "monthly", "A", 20000, 8200)
    assert pick(c["C-KR-1088"], *keys) == ("한빛코스메틱", "seller", "ko", "monthly", "B", 15000, 3100)
    assert pick(c["C-CN-3150"], *keys) == ("林晓", "individual", "zh", "prepaid", None, None, None)


def test_seed_shipments(world):
    s = world.shipments
    keys = ("customer_id", "direction", "mode", "goods", "status", "warehouse_job")
    assert pick(s["FWQD2611043"], *keys) == (
        "C-KR-0412", "import", "express_ferry", "의류 3점", "customs_hold", None
    )
    assert pick(s["FWQD2611150"], *keys) == (
        "C-CN-3150", "import", "express_ferry", "保温杯 2 个", "customs_inspection", None
    )
    assert pick(s["FWICN2611088"], *keys) == (
        "C-KR-1088", "export", "ferry_lcl", "化妆品（护肤品）", "in_transit", None
    )
    assert pick(s["FWQD2611127"], *keys) == (
        "C-CN-2207", "import", "ferry_lcl", "家居收纳用品", "at_warehouse", "WJ-2611-031"
    )
    assert s["FWQD2611043"]["declared_value_usd"] == 120 and len(s["FWQD2611043"]["events"]) == 3
    assert s["FWQD2611150"]["declared_value_usd"] == 46
    assert pick(s["FWICN2611088"], "packages", "gross_weight_kg", "volume_cbm") == (48, 680, 3.2)
    assert s["FWICN2611088"]["eta"].startswith("2026-11-19T")
    assert s["FWQD2611127"]["packages"] == 120
    routes = {hbl: (r["origin"]["name_zh"], r["destination"]["name_zh"]) for hbl, r in s.items()}
    assert routes["FWQD2611043"] == ("青岛", "仁川") and routes["FWICN2611088"] == ("仁川", "青岛")


def test_seed_customs_progress(world):
    held = world.customs["FWQD2611043"]
    assert pick(held, "cargo_no", "arrival_customs", "bonded_warehouse", "clearance_type") == (
        "26FWQD1104-0043", "인천세관", "인천 보세창고", "목록통관"
    )
    assert [(s["label_ko"], s["status"], s["at"]) for s in held["steps"]] == [
        ("입항보고", "done", "2026-11-16T08:20:00+09:00"),
        ("하선신고 수리", "done", "2026-11-16T13:05:00+09:00"),
        ("반입신고", "done", "2026-11-16T18:40:00+09:00"),
        ("수입신고", "pending", None),
        ("수입신고 수리", "pending", None),
        ("반출신고", "pending", None),
    ]
    assert pick(held["hold"], "code", "since", "status") == (
        "pccc_mismatch", "2026-11-17T09:10:00+09:00", "open"
    )
    assert all(held["hold"][key] for key in ("reason_ko", "reason_zh", "required_action"))
    inspected = world.customs["FWQD2611150"]
    assert [s["status"] for s in inspected["steps"]] == ["done"] * 3 + ["pending"] * 3
    assert pick(inspected["hold"], "code", "status", "required_action") == (
        "inspection", "open", "等待海关查验结果，无需客户操作"
    )
    cleared = world.customs["FWQD2611127"]
    assert [s["status"] for s in cleared["steps"]] == ["done"] * 6 and cleared["hold"] is None
    for record in world.customs.values():
        assert [s["key"] for s in record["steps"]] == CUSTOMS_STEP_KEYS


def test_seed_warehouse_job_and_damage_photos(world):
    job = world.warehouse_jobs["WJ-2611-031"]
    assert pick(job, "customer_id", "hbl", "requested_actions") == ("C-CN-2207", "FWQD2611127", [])
    assert [(s["key"], s["label_ko"], s["status"]) for s in job["steps"]] == [
        ("inbound", "입고", "done"),
        ("inspection", "검수", "done"),
        ("labeling", "라벨링", "pending"),
        ("packing", "포장", "pending"),
        ("storage", "보관", "pending"),
        ("outbound", "출고", "pending"),
    ]
    inspection = job["steps"][1]
    assert pick(inspection, "result", "cartons", "attachments") == (
        "damaged", ["C-014", "C-027", "C-031"], ["ATT-9001", "ATT-9002", "ATT-9003"]
    )
    for attachment_id, carton in zip(inspection["attachments"], inspection["cartons"]):
        photo = world.attachments[attachment_id]
        assert pick(photo, "customer_id", "type", "carton") == ("C-CN-2207", "photo", carton)
        assert carton in photo["description_zh"] and carton in photo["description_ko"]


def test_every_step_has_the_shared_format(world):
    steps = [s for record in world.customs.values() for s in record["steps"]]
    steps += [s for job in world.warehouse_jobs.values() for s in job["steps"]]
    for step in steps:
        assert {"key", "label_ko", "label_zh", "status", "at"} <= step.keys()
        assert step["label_ko"] and step["label_zh"] and step["status"] in ("done", "pending")
        assert (step["at"] is None) == (step["status"] == "pending")


def test_every_seed_timestamp_is_iso_8601_at_plus_nine(world):
    tables = [getattr(world, field.name) for field in dataclasses.fields(world)]
    times = [text for text in strings_in(tables) if re.match(r"\d{4}-\d{2}-\d{2}", text)]
    assert "2026-11-17T09:10:00+09:00" in times
    for text in times:
        assert text.endswith("+09:00"), text
        assert datetime.fromisoformat(text).utcoffset() == timedelta(hours=9), text


def test_world_data_is_frozen_and_replaceable(world):
    with pytest.raises(dataclasses.FrozenInstanceError):
        world.customs = {}
    variant = dataclasses.replace(world, customs={})
    assert variant.customs == {} and variant.shipments is world.shipments and world.customs


def test_load_world_rejects_duplicate_keys(tmp_path):
    for name in ("customers", "shipments", "customs", "warehouse_jobs", "attachments"):
        (tmp_path / f"{name}.json").write_text("[]", encoding="utf-8")
    assert load_world(tmp_path).shipments == {}
    (tmp_path / "shipments.json").write_text(json.dumps([{"hbl": "FWQD0000001"}] * 2), encoding="utf-8")
    with pytest.raises(ValueError, match="FWQD0000001"):
        load_world(tmp_path)


# Lookups and overlays (view.py)


def test_lookups_filter_by_customer_and_return_none_when_missing(world):
    v = WorldView(world, [])
    assert [s["hbl"] for s in v.shipments_for("C-CN-2207")] == ["FWQD2611127"]
    assert [s["hbl"] for s in v.shipments_for("C-KR-0412")] == ["FWQD2611043"]
    assert [j["job_id"] for j in v.warehouse_jobs_for("C-CN-2207")] == ["WJ-2611-031"]
    assert v.shipments_for("C-XX-0000") == [] and v.warehouse_jobs_for("C-KR-0412") == []
    assert v.customer("C-KR-1088")["name"] == "한빛코스메틱"
    assert v.attachment("ATT-9002")["carton"] == "C-027"
    assert v.customs("FWICN2611088") is None  # an export: there is no import clearance record
    assert v.customer("C-XX-0000") is None and v.shipment("FWQD0000000") is None
    assert v.customs("FWQD0000000") is None and v.warehouse_job("WJ-0000-000") is None
    assert v.attachment("ATT-0000") is None


def test_pccc_overlay_appends_the_step_and_leaves_the_seed_alone(world, db):
    led = Ledger(db)
    record = led.stage(namespace="R-1", ticket_id="T-1", tool="customs.submit_pccc", payload=PCCC_PAYLOAD)
    seed = copy.deepcopy(world.customs["FWQD2611043"])
    c = WorldView(world, led.effects("R-1")).customs("FWQD2611043")
    assert c["hold"] == {**seed["hold"], "status": "resubmitted", "resubmitted_at": record.created_at}
    assert c["steps"] == seed["steps"] + [{
        "key": "pccc_resubmitted",
        "label_ko": "개인통관고유부호 정정 제출",
        "label_zh": "已提交更正后的个人通关号码",
        "status": "done",
        "at": record.created_at,
    }]
    assert world.customs["FWQD2611043"] == seed
    led.commit("R-1")
    assert WorldView(world, led.effects()).customs("FWQD2611043") == c


def test_pccc_overlay_needs_a_pccc_hold_on_the_same_hbl(world, db):
    led = Ledger(db)
    for hbl in ("FWQD2611150", "FWQD2611127"):  # an inspection hold, and no hold at all
        led.stage(
            namespace="R-1", ticket_id="T-1", tool="customs.submit_pccc",
            payload={"hbl": hbl, "pccc_masked": "P1907****0455"},
        )
    led.stage(
        namespace="R-1", ticket_id="T-1", tool="ticket.handoff", payload={"reason": "客户要求人工"}
    )
    v = WorldView(world, led.effects("R-1"))
    for hbl in world.customs:
        assert v.customs(hbl) == world.customs[hbl]


def test_warehouse_overlay_marks_requests_pending_until_committed(world, db):
    led = Ledger(db)
    repack = {"job_id": "WJ-2611-031", "action": "repack", "cartons": ["C-014", "C-027"], "note": "换新外箱"}
    first = led.stage(namespace="R-1", ticket_id="T-1", tool="warehouse.request_action", payload=repack)
    led.commit("R-1")
    hold = {"job_id": "WJ-2611-031", "action": "hold"}  # cartons and note left out
    second = led.stage(namespace="R-2", ticket_id="T-1", tool="warehouse.request_action", payload=hold)
    job = WorldView(world, led.effects("R-2")).warehouse_job("WJ-2611-031")
    assert job["requested_actions"] == [
        dict(action="repack", cartons=["C-014", "C-027"], note="换新外箱", at=first.created_at, pending=False),
        dict(action="hold", cartons=[], note=None, at=second.created_at, pending=True),
    ]
    assert job["steps"] == world.warehouse_jobs["WJ-2611-031"]["steps"]
    # warehouse_jobs_for applies the same overlay; R-2's staged request stays inside R-2.
    jobs = WorldView(world, led.effects()).warehouse_jobs_for("C-CN-2207")
    assert [j["requested_actions"] for j in jobs] == [job["requested_actions"][:1]]


def test_overlaid_results_are_copies_too(world, db):
    led = Ledger(db)
    led.stage(namespace="R-1", ticket_id="T-1", tool="customs.submit_pccc", payload=PCCC_PAYLOAD)
    led.stage(
        namespace="R-1", ticket_id="T-1", tool="warehouse.request_action",
        payload={"job_id": "WJ-2611-031", "action": "repack", "cartons": ["C-014"]},
    )
    effects = led.effects("R-1")
    seed, payloads = copy.deepcopy(world), copy.deepcopy([e.payload for e in effects])
    v = WorldView(world, effects)
    before = (v.customs("FWQD2611043"), v.warehouse_job("WJ-2611-031"), v.shipments_for("C-KR-0412"))
    v.customs("FWQD2611043")["hold"]["status"] = "x"
    v.customs("FWQD2611043")["steps"].clear()
    v.warehouse_job("WJ-2611-031")["requested_actions"][0]["cartons"].append("C-999")
    v.warehouse_jobs_for("C-CN-2207")[0]["steps"][1]["cartons"].clear()
    v.shipments_for("C-KR-0412")[0]["events"].clear()
    v.customer("C-KR-0412")["name"] = "x"
    v.attachment("ATT-9001")["carton"] = "x"
    assert (v.customs("FWQD2611043"), v.warehouse_job("WJ-2611-031"), v.shipments_for("C-KR-0412")) == before
    assert world == seed and [e.payload for e in effects] == payloads
