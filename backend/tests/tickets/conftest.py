import pytest

from forwarder.agent.loop import AgentRunner
from forwarder.llm.scripted import ScriptedModel, reply_text
from forwarder.tickets.events import EventBus
from forwarder.tickets.executor import InlineExecutor, ManualExecutor
from forwarder.tickets.repo import TicketRepo
from forwarder.tickets.service import TicketService
from forwarder.tools.logistics import P1_TOOLS, register_logistics_tools
from forwarder.tools.registry import ToolRegistry
from forwarder.world.ledger import Ledger


class HookedScriptedModel(ScriptedModel):
    """ScriptedModel that can run a function at the start of a given call, counting calls from 0."""

    def __init__(self, replies):
        super().__init__(replies)
        self._hooks = {}

    def on_call(self, index, fn):
        self._hooks[index] = fn

    def complete(self, messages, tools):
        hook = self._hooks.pop(len(self.calls), None)  # len(self.calls) is this call's index
        if hook is not None:
            hook()
        return super().complete(messages, tools)


@pytest.fixture
def make_svc(db, world, settings):
    """make_svc(model_factory, executor, *, auto_reply=True, max_steps=10) -> (svc, repo, bus, ledger).

    A TicketService over the temporary database and the seed world, offering the P1 tools.
    """

    def make(model_factory, executor, *, auto_reply=True, max_steps=10):
        settings.auto_reply_readonly = auto_reply
        registry = ToolRegistry()
        register_logistics_tools(registry)
        repo, ledger, bus = TicketRepo(db), Ledger(db), EventBus()
        runner = AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=max_steps)
        svc = TicketService(
            repo=repo, ledger=ledger, runner=runner, bus=bus, world=world, settings=settings,
            model_factory=model_factory, executor=executor, tool_names=P1_TOOLS,
        )
        return svc, repo, bus, ledger

    return make


@pytest.fixture
def svc_factory(make_svc):
    """svc_factory(replies, auto_reply=True, return_model=False, max_steps=10).

    Runs go inline, every one on the same ScriptedModel(replies). Returns (svc, repo, bus, ledger),
    and the model as well when return_model is true.
    """

    def factory(replies, auto_reply=True, return_model=False, max_steps=10):
        model = ScriptedModel(replies)
        svc, repo, bus, ledger = make_svc(lambda: model, InlineExecutor(), auto_reply=auto_reply, max_steps=max_steps)
        return (svc, repo, bus, ledger, model) if return_model else (svc, repo, bus, ledger)

    return factory


@pytest.fixture
def svc_factory_manual(make_svc):
    """svc_factory_manual(replies=None) -> (svc, repo, executor, model).

    Runs wait in a ManualExecutor until run_next(). The model is a HookedScriptedModel, which by
    default replies "첫 답변" and then "두 번째 답변".
    """

    def factory(replies=None):
        if replies is None:
            replies = [reply_text("첫 답변"), reply_text("두 번째 답변")]
        model = HookedScriptedModel(replies)
        executor = ManualExecutor()
        svc, repo, _, _ = make_svc(lambda: model, executor)
        return svc, repo, executor, model

    return factory
