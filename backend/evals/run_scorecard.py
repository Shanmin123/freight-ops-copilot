"""Run the production scorecard end-to-end in an isolated local database."""

import argparse
import asyncio
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from httpx import ASGITransport, AsyncClient

from forwarder.config import Settings, load_settings
from forwarder.main import create_app


async def run(profiles: list[str], sample: int, threshold: int) -> int:
    settings = load_settings() if set(profiles) - {"mock"} else Settings("mock", None, None)
    with TemporaryDirectory(prefix="forwarder-score-cli-") as directory:
        app = create_app(replace(settings, db_path=Path(directory) / "score.sqlite3"))
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://score") as client:
                request = await client.post("/api/evals/batches", json={
                    "profiles": profiles, "sample": sample, "threshold": threshold,
                })
                batch_id = request.raise_for_status().json()["id"]
                while True:
                    batch = (await client.get(f"/api/evals/batches/{batch_id}")).raise_for_status().json()
                    if batch["status"] != "running":
                        break
                    await asyncio.sleep(0.6)
                print("| 模型 | 案例 | 通过 | 平均分 | 封顶 | Token | 高峰 USD |")
                print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
                for row in batch["summary"]:
                    print(f"| {row['label']} | {row['count']} | {row['passed']} | {row['mean']} | "
                          f"{row['cap_count']} | {row['tokens']} | {row['peak_usd']} |")
                for row in batch["rows"]:
                    if row["points"] < threshold:
                        print(f"- {row['profile']}/{row['case_id']} ({row['points']}): {'；'.join(row['problems'])}")
                return 0 if batch["status"] == "complete" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", nargs="+", default=["flash"])
    parser.add_argument("--sample", type=int, default=12)
    parser.add_argument("--threshold", type=int, default=70)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.profiles, args.sample, args.threshold)))
