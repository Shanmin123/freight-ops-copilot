"""WorldView: the seed world with ledger effects laid over it."""

import copy

from .data import WorldData
from .ledger import STAGED, LedgerRecord

_PCCC_RESUBMITTED_STEP = {
    "key": "pccc_resubmitted",
    "label_ko": "개인통관고유부호 정정 제출",
    "label_zh": "已提交更正后的个人通关号码",
    "status": "done",
}


class WorldView:
    """The business world as it looks once effects (normally Ledger.effects(namespace)) are applied.

    The seed world is never changed. Every lookup returns a fresh deep copy, or None when nothing
    matches; lists come in seed order.
    """

    def __init__(self, world: WorldData, effects: list[LedgerRecord]) -> None:
        self._world = world
        self._effects = list(effects)

    def customer(self, customer_id: str) -> dict | None:
        return copy.deepcopy(self._world.customers.get(customer_id))

    def shipments_for(self, customer_id: str) -> list[dict]:
        return [self.shipment(hbl) for hbl, item in self._world.shipments.items() if item["customer_id"] == customer_id]

    def shipment(self, hbl: str) -> dict | None:
        shipment = copy.deepcopy(self._world.shipments.get(hbl))
        if shipment is not None:
            for tool, field in (("delivery.reroute", "delivery_reroute"),
                                ("carrier.open_investigation", "carrier_investigation")):
                for effect in self._effects_on(tool, "hbl", hbl):
                    shipment[field] = {**copy.deepcopy(effect.payload), "pending": effect.status == STAGED}
        return shipment

    def customs(self, hbl: str) -> dict | None:
        record = copy.deepcopy(self._world.customs.get(hbl))
        if record is not None:
            for effect in self._effects_on("customs.submit_pccc", "hbl", hbl):
                _mark_pccc_resubmitted(record, effect)
        return record

    def warehouse_job(self, job_id: str) -> dict | None:
        job = copy.deepcopy(self._world.warehouse_jobs.get(job_id))
        if job is not None:
            for effect in self._effects_on("warehouse.request_action", "job_id", job_id):
                _add_requested_action(job, effect)
        return job

    def warehouse_jobs_for(self, customer_id: str) -> list[dict]:
        jobs = self._world.warehouse_jobs.items()
        return [self.warehouse_job(job_id) for job_id, job in jobs if job["customer_id"] == customer_id]

    def attachment(self, attachment_id: str) -> dict | None:
        return copy.deepcopy(self._world.attachments.get(attachment_id))

    def _effects_on(self, tool: str, field: str, value: str) -> list[LedgerRecord]:
        return [e for e in self._effects if e.tool == tool and e.payload.get(field) == value]


def _mark_pccc_resubmitted(record: dict, effect: LedgerRecord) -> None:
    """A corrected PCCC was handed to the broker: only a PCCC-mismatch hold reacts to it."""
    hold = record.get("hold")
    if not hold or hold.get("code") != "pccc_mismatch":
        return
    hold["status"] = "resubmitted"
    hold["resubmitted_at"] = effect.created_at
    record["steps"].append({**_PCCC_RESUBMITTED_STEP, "at": effect.created_at})


def _add_requested_action(job: dict, effect: LedgerRecord) -> None:
    payload = effect.payload
    action = {
        "action": payload.get("action"),
        "cartons": payload.get("cartons") or [],
        "note": payload.get("note"),
        "at": effect.created_at,
        "pending": effect.status == STAGED,  # not yet confirmed by staff
    }
    # A copy, so the result shares nothing with the ledger record's payload.
    job.setdefault("requested_actions", []).append(copy.deepcopy(action))
