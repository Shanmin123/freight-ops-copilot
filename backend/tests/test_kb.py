import json
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from forwarder.kb import KnowledgeBase, knowledge_answer
from forwarder.llm.mock_demo import DemoMockModel
from forwarder.llm.scripted import ScriptedModel, reply_text, reply_tool
from forwarder.main import create_app
from forwarder.tickets.executor import InlineExecutor


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_lexical_search_uses_bilingual_aliases_and_filters_unsupported_queries(tmp_path):
    path = tmp_path / "articles.json"
    path.write_text(json.dumps([{
        "id": "pccc-correction",
        "title": {"zh": "通关号码更正", "ko": "통관부호 정정"},
        "sections": [{
            "id": "correction",
            "title": {"zh": "办理步骤", "ko": "정정 방법"},
            "text": {"zh": "先联系承运商。", "ko": "운송사에 먼저 문의하세요."},
            "aliases": ["更正通关号码", "개인통관고유부호 정정"],
            "source": {"label": "Korea Customs Service", "url": "https://www.customs.go.kr/"},
        }],
    }], ensure_ascii=False), encoding="utf-8")
    kb = KnowledgeBase.load(path)
    [hit] = kb.search("개인통관 고유부호를 정정하는 방법이 궁금해요")
    assert hit["text"]["zh"] == "先联系承运商。"
    assert "aliases" not in kb.articles[0]["sections"][0]
    assert kb.search("海关移动应急检查站的排班") == []


def test_source_lines_are_built_from_retrieved_sections():
    hit = {
        "article_id": "pccc-correction", "section_id": "correction",
        "title": {"zh": "号码更正", "ko": "통관부호 정정"},
        "section_title": {"zh": "办理步骤", "ko": "방법"},
        "text": {"zh": "请先联系客服。", "ko": "운송사에 문의하세요."},
    }
    assert knowledge_answer("捏造内容 [9]", [hit], "zh") == (
        "请先联系客服。\n\n来源：[号码更正 · 办理步骤](/kb?doc=pccc-correction#correction)"
    )
    assert knowledge_answer("운송사에 문의하세요. [1]", [hit], "ko").endswith(
        "출처: [통관부호 정정 · 방법](/kb?doc=pccc-correction&lang=ko#correction)"
    )
    assert "没有找到" in knowledge_answer("编造的答案 [1]", [], "zh")


@pytest.mark.anyio
async def test_mock_knowledge_in_same_ticket_preserves_pending_business_writes(settings):
    settings = replace(settings, llm_provider="mock", auto_reply_readonly=True)
    app = create_app(settings, executor=InlineExecutor(), model_factory=DemoMockModel)
    ident = {"customer_id": "C-KR-0412", "channel": "kakaotalk"}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            async def send(text):
                return (await client.post("/api/chat/messages", json={**ident, "text": text})).raise_for_status().json()

            tid = (await send("FWQD2611043 통관이 왜 보류됐나요?"))["ticket_id"]
            assert (await send("개인통관고유부호를 정정하는 방법은 무엇인가요?"))["ticket_id"] == tid
            path = f"/api/tickets/{tid}"
            detail = (await client.get(path)).raise_for_status().json()
            assert detail["ticket"]["status"] == "ai_replied"
            steps = detail["runs"][-1]["steps"]
            assert any(step["kind"] == "tool" and step["data"]["name"] == "kb.search" for step in steps)
            assert "/kb?doc=pccc-correction&lang=ko#correction" in detail["messages"][-1]["text"]
            assert (await client.get("/api/kb/articles")).raise_for_status().json()["articles"]

            await send("P230518447201")
            detail = (await client.get(path)).raise_for_status().json()
            assert detail["ticket"]["status"] == "awaiting_confirmation"
            draft = next(m for m in detail["messages"] if m["status"] == "draft")
            assert (await client.post(f"{path}/messages/{draft['id']}/confirm", json={})).raise_for_status()


@pytest.mark.anyio
async def test_knowledge_search_keeps_mixed_language_customer_intent(settings):
    def model():
        return ScriptedModel([
            reply_tool("kb.search", {"query": "한국 목록통관 적용되지 않는 상품 품목"}),
            reply_text(" [1]"),
        ])

    settings = replace(settings, auto_reply_readonly=True)
    app = create_app(settings, executor=InlineExecutor(), model_factory=model)
    ident = {"customer_id": "C-CN-3150", "channel": "web"}
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.post("/api/chat/messages", json={
                **ident, "text": "韩国的 목록통관 有哪些不适用的商品？",
            })
            assert response.status_code == 200
            conversation = (await client.get("/api/chat/conversation", params=ident)).raise_for_status().json()
            reply = conversation["messages"][-1]
            assert "健康功能食品" in reply["text"]
            assert "/kb?doc=list-clearance#exclusions" in reply["text"]
