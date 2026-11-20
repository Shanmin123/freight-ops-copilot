"""Seed data of the demo world: WorldData and load_world."""

import json
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path

# Every timestamp in the demo world carries this offset.
WORLD_TZ = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class WorldData:
    """The seed tables, each mapping a record's key to the record.

    WorldView reads them and never changes them.
    """

    customers: dict[str, dict]  # by "id"
    shipments: dict[str, dict]  # by "hbl"
    customs: dict[str, dict]  # by "hbl"
    warehouse_jobs: dict[str, dict]  # by "job_id"
    attachments: dict[str, dict]  # by "attachment_id"


# WorldData field -> the record key its table is indexed by. The table is read from "<field>.json".
_KEYS = {
    "customers": "id",
    "shipments": "hbl",
    "customs": "hbl",
    "warehouse_jobs": "job_id",
    "attachments": "attachment_id",
}


def load_world(world_dir: Path) -> WorldData:
    """Read each table from world_dir/<field>.json, a UTF-8 JSON list of records, keeping file order."""
    world_dir = Path(world_dir)
    tables = {field: _load_table(world_dir / f"{field}.json", key) for field, key in _KEYS.items()}
    return WorldData(**tables)


def _load_table(path: Path, key: str) -> dict[str, dict]:
    table: dict[str, dict] = {}
    for record in json.loads(path.read_text(encoding="utf-8")):
        if record[key] in table:
            raise ValueError(f"{path.name}: duplicate {key} {record[key]!r}")
        table[record[key]] = record
    return table
