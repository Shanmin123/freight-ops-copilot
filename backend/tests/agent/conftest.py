import pytest

from forwarder.agent.context import TicketContext
from forwarder.agent.loop import AgentRunner
from forwarder.tools.logistics import register_logistics_tools
from forwarder.tools.registry import ToolRegistry
from forwarder.world.ledger import Ledger


@pytest.fixture
def ledger(db):
    return Ledger(db)


@pytest.fixture
def registry():
    reg = ToolRegistry()
    register_logistics_tools(reg)
    return reg


@pytest.fixture
def runner(registry, ledger, world):
    return AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=10)


@pytest.fixture
def runner_max2(registry, ledger, world):
    return AgentRunner(registry=registry, ledger=ledger, world=world, max_steps=2)


@pytest.fixture
def ctx_kr_ticket():
    # C-KR-0412 owns FWQD2611043, held in customs for a PCCC mismatch.
    return TicketContext("T-9", "kakaotalk", "C-KR-0412", "김지우", [], "…")
