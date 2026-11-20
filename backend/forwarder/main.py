"""Application factory for the local forwarder demo."""

from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from math import isfinite

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from forwarder.agent.loop import AgentRunner
from forwarder.api.routes import build_router
from forwarder.automation import MailAutomation, build_automation_router
from forwarder.config import BACKEND_DIR, Settings, load_settings
from forwarder.db import Database
from forwarder.delivery import DELIVERY_TOOLS, register_delivery_tools
from forwarder.documents import DOCUMENT_TOOL, DocumentStore, build_documents_router, register_document_tool
from forwarder.kb import KB_TOOL, KnowledgeBase, register_knowledge_tool
from forwarder.llm.base import ChatModel
from forwarder.llm.factory import build_agent_model
from forwarder.quotes import QUOTE_TOOLS, QuoteService, build_quote_router, register_quote_tools
from forwarder.scorecard import Scorecard, build_scorecard_router
from forwarder.tickets.events import EventBus
from forwarder.tickets.executor import Executor, ThreadExecutor
from forwarder.tickets.repo import TicketRepo
from forwarder.tickets.service import TicketService
from forwarder.tools.logistics import P1_TOOLS, register_logistics_tools
from forwarder.tools.registry import ToolRegistry
from forwarder.world.data import load_world
from forwarder.world.ledger import Ledger

DIST_DIR = BACKEND_DIR.parent / "frontend" / "dist"


def create_app(
    settings: Settings | None = None,
    *,
    executor: Executor | None = None,
    model_factory: Callable[[], ChatModel] | None = None,
    enable_scorecard: bool = True,
) -> FastAPI:
    settings = load_settings() if settings is None else replace(settings)
    if settings.sse_ping_seconds <= 0 or not isfinite(settings.sse_ping_seconds):
        raise ValueError("SSE_PING_SECONDS must be a finite, positive number")

    world = load_world(settings.world_dir)
    seed_path = settings.world_dir / "seed_tickets.json"
    registry = ToolRegistry()
    register_logistics_tools(registry)
    register_delivery_tools(registry)
    knowledge = KnowledgeBase.load()
    register_knowledge_tool(registry, knowledge)
    db = Database(settings.db_path)
    owned_executor = ThreadExecutor() if executor is None else None
    runner_executor = owned_executor if owned_executor is not None else executor
    automation = scorecard = None
    try:
        db.init_schema()
        repo, ledger, bus = TicketRepo(db), Ledger(db), EventBus()
        quotes = QuoteService(db, ledger)
        register_quote_tools(registry, quotes)
        documents = DocumentStore(db, repo, settings)
        register_document_tool(registry, documents)
        runner = AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=settings.agent_max_steps)
        service = TicketService(
            repo=repo, ledger=ledger, runner=runner, bus=bus, world=world, settings=settings,
            model_factory=model_factory if model_factory is not None else lambda: build_agent_model(settings),
            executor=runner_executor, tool_names=[*P1_TOOLS, KB_TOOL, *DELIVERY_TOOLS, *QUOTE_TOOLS, DOCUMENT_TOOL],
        )
        automation = MailAutomation(db=db, repo=repo, service=service, world=world)
        service.on_confirmed = automation.on_confirmation
        if enable_scorecard:
            from forwarder.tickets.executor import InlineExecutor

            scorecard = Scorecard(db, settings, lambda isolated: create_app(
                isolated, executor=InlineExecutor(), enable_scorecard=False,
            ))
        if not repo.list_tickets():
            service.reset_demo(seed_path)
    except BaseException:
        if owned_executor is not None:
            owned_executor.shutdown()
        if scorecard is not None:
            scorecard.close()
        if automation is not None:
            automation.close()
        db.close()
        raise

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owned_executor is not None:
                owned_executor.shutdown()
            if scorecard is not None:
                scorecard.close()
            automation.close()
            db.close()

    app = FastAPI(
        title="Forwarder Agent", lifespan=lifespan, docs_url="/api/docs", redoc_url="/api/redoc",
        openapi_url="/api/openapi.json", swagger_ui_oauth2_redirect_url="/api/docs/oauth2-redirect",
    )
    app.include_router(build_router(
        settings=settings, service=service, repo=repo, bus=bus,
        ledger=ledger, world=world, seed_path=seed_path,
        reset_extras=tuple([documents.clear, automation.reset] + ([scorecard.clear] if scorecard else [])),
    ))
    app.include_router(build_quote_router(quotes))
    app.include_router(build_documents_router(documents))
    app.include_router(build_automation_router(automation))
    if scorecard is not None:
        app.include_router(build_scorecard_router(scorecard))

    @app.get("/api/kb/articles")
    def knowledge_articles():
        return {"articles": knowledge.articles}

    @app.get("/api/kb/search")
    def knowledge_search(q: str = Query(min_length=1, max_length=500)):
        return {"hits": knowledge.search(q), "mode": knowledge.search_mode}

    dist = DIST_DIR.resolve()
    index = dist / "index.html"
    if index.is_file():
        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/"):
                raise HTTPException(404, "接口不存在")
            asset = (dist / path).resolve()
            if not asset.is_relative_to(dist):
                raise HTTPException(404, "页面不存在")
            return FileResponse(asset if asset.is_file() else index)

    return app
