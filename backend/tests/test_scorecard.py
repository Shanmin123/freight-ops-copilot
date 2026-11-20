import asyncio
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from forwarder.main import create_app
from forwarder.score_cases import make_cases
from forwarder.scorecard import grade_ticket


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_generated_cases_have_unique_ids_and_an_independent_score_cap():
    cases = make_cases()
    assert len(cases) == len({case["id"] for case in cases}) == 80
    assert {case["mode"] for case in cases if "mode" in case} == {"fcl", "lcl", "ferry", "air"}
    case = next(case for case in cases if case["group"] == "shipment")
    data = {"hbl": case["value"]}
    tool = {"kind": "tool", "data": {"name": case["tool"], "observation": {"ok": True, "data": data}}}
    run = {"steps": [tool], "write_calls": [], "usage": {}, "model": "mock"}
    detail = {"runs": [run], "ticket": {"status": "ai_replied"}, "messages": [
        {"role": "agent", "kind": "normal", "text": "已经查到货物进度。"},
    ]}
    assert grade_ticket(case, detail)["points"] == 100
    run["write_calls"].append("delivery.reroute")
    assert grade_ticket(case, detail)["points"] == 30


@pytest.mark.anyio
async def test_api_mode_scorecard_only_offers_real_models(settings):
    app = create_app(replace(settings, llm_provider="deepseek", deepseek_api_key="test-key"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            catalog = (await client.get("/api/evals/catalog")).raise_for_status().json()
            assert {entry["id"] for entry in catalog["profiles"]} == {"flash", "flash-thinking", "pro"}
            assert (await client.post("/api/evals/batches", json={"profiles": ["mock"]})).status_code == 422


@pytest.mark.anyio
async def test_scorecard_persists_and_replays_a_mock_batch(settings):
    app = create_app(replace(settings, llm_provider="mock"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            created = await client.post("/api/evals/batches", json={"profiles": ["mock"], "sample": 1})
            identifier = created.raise_for_status().json()["id"]
            for _ in range(100):
                batch = (await client.get(f"/api/evals/batches/{identifier}")).raise_for_status().json()
                if batch["status"] != "running":
                    break
                await asyncio.sleep(0.1)
            assert batch["status"] == "complete"
            row = batch["rows"][0]
            replay = (await client.get(f"/api/evals/batches/{identifier}/replay/mock/{row['case_id']}")).raise_for_status().json()
            assert replay["steps"] and replay["points"] == 100
