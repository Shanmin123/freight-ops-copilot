"""Run the knowledge retrieval and chat acceptance cases."""

import argparse
import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TypedDict
from urllib.parse import parse_qs, urlsplit

from httpx import ASGITransport, AsyncClient

from forwarder.config import Settings, load_settings
from forwarder.kb import KnowledgeBase
from forwarder.main import create_app
from forwarder.tickets.executor import InlineExecutor


CASE_PATH = Path(__file__).with_name("knowledge_cases.json")
REFERENCE = re.compile(r"\]\((/kb\?doc=[^)\s]+)\)")
LANGUAGE = {"zh": re.compile(r"[\u3400-\u9fff]"), "ko": re.compile(r"[\uac00-\ud7a3]")}
CUSTOMER = {"zh": "C-CN-3150", "ko": "C-KR-0412"}


class Case(TypedDict):
    id: str
    language: str
    question: str
    expected: str | None
    terms: list[str]


def reference_ids(reply: str) -> set[str]:
    references = set()
    for match in REFERENCE.findall(reply):
        url = urlsplit(match)
        article = parse_qs(url.query, keep_blank_values=True)["doc"][0]
        references.add(f"{article}/{url.fragment}")
    return references


async def evaluate_case(
    client: AsyncClient, kb: KnowledgeBase, case: Case
) -> tuple[str | None, bool, str | None]:
    hits = kb.search(case["question"])
    retrieved = [f"{hit['article_id']}/{hit['section_id']}" for hit in hits]
    top = retrieved[0] if retrieved else None
    expected = case["expected"]
    retrieval_passed = top == expected

    # Start every question in a fresh conversation on the same app and corpus.
    (await client.post("/api/demo/reset")).raise_for_status()
    identity = {"customer_id": CUSTOMER[case["language"]], "channel": "web"}
    response = await client.post("/api/chat/messages", json={**identity, "text": case["question"]})
    ticket_id = response.raise_for_status().json()["ticket_id"]
    detail = (await client.get(f"/api/tickets/{ticket_id}")).raise_for_status().json()
    conversation = (await client.get("/api/chat/conversation", params=identity)).raise_for_status().json()
    reply = next(
        (message["text"] for message in reversed(conversation["messages"])
         if message["role"] == "agent" and message["kind"] == "normal" and message["status"] == "sent"),
        "",
    )
    calls = {
        step["data"]["name"]
        for run in detail["runs"]
        for step in run["steps"]
        if step["kind"] == "tool"
    }
    references = reference_ids(reply)
    answer = "\n".join(line for line in reply.splitlines() if not REFERENCE.search(line))
    has_expected_terms = any(term in answer for term in case["terms"])
    counts = {language: len(pattern.findall(answer)) for language, pattern in LANGUAGE.items()}
    language_matches = counts[case["language"]] > 0 and counts[case["language"]] == max(counts.values())
    if expected is None:
        sources_match = not references
    else:
        sources_match = expected in references and references.issubset(retrieved)
    problems = [
        reason for failed, reason in (
            ("kb.search" not in calls, "未调用知识库"),
            (detail["ticket"]["status"] != "ai_replied", f"工单状态 {detail['ticket']['status']}"),
            (not has_expected_terms, "缺少回答要点"),
            (not language_matches, "回复语言不符合预期"),
            (not sources_match, f"引用不符合预期 ({', '.join(sorted(references)) or '无'})"),
        ) if failed
    ]
    search_steps = [
        step["data"]["arguments"].get("query")
        for run in detail["runs"] for step in run["steps"]
        if step["kind"] == "tool" and step["data"]["name"] == "kb.search"
    ]
    failure = f"{'、'.join(problems)}。实际检索 {search_steps!r}，回复 {answer!r}。" if problems else None
    return top, retrieval_passed, failure


async def evaluate(provider: str, selected: list[str]) -> int:
    cases: list[Case] = json.loads(CASE_PATH.read_text(encoding="utf-8"))
    if selected:
        unknown = set(selected) - {case["id"] for case in cases}
        if unknown:
            raise SystemExit(f"Unknown cases: {', '.join(sorted(unknown))}")
        cases = [case for case in cases if case["id"] in selected]

    if provider == "deepseek":
        settings = load_settings()
        if settings.llm_provider != "deepseek":
            raise SystemExit("DeepSeek is not configured.")
        settings = replace(settings, auto_reply_readonly=True)
    else:
        # The default benchmark is independent of .env and model credentials.
        settings = Settings(llm_provider="mock", llm_fallback_reason=None, deepseek_api_key=None)

    kb = KnowledgeBase.load()
    retrieval_passes = 0
    reply_passes = 0
    failures = []
    print("| 案例 | 预期首条 | 实际首条 | 检索 | 客服回复 |")
    print("| --- | --- | --- | --- | --- |")
    with TemporaryDirectory(prefix="forwarder-kb-eval-") as directory:
        app = create_app(replace(settings, db_path=Path(directory) / "eval.sqlite3"), executor=InlineExecutor())
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://testserver") as client:
                for case in cases:
                    top, retrieval_passed, failure = await evaluate_case(client, kb, case)
                    retrieval_passes += retrieval_passed
                    reply_passes += failure is None
                    if failure:
                        failures.append((case["id"], failure))
                    print(
                        f"| {case['id']} | {case['expected'] or '无'} | {top or '无'} | "
                        f"{'通过' if retrieval_passed else '失败'} | {'失败' if failure else '通过'} |"
                    )

    print(f"\n{provider}：检索 {retrieval_passes}/{len(cases)}，客服回复 {reply_passes}/{len(cases)}。")
    for case_id, failure in failures:
        print(f"- {case_id}：{failure}")
    return 0 if retrieval_passes == reply_passes == len(cases) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("mock", "deepseek"), default="deepseek")
    parser.add_argument("--case", action="append", default=[], help="Run a case by ID. Repeat to select more.")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(evaluate(args.provider, args.case)))
