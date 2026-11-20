import asyncio
import json

import pytest

from forwarder.api.sse import customer_visible, event_stream, format_sse
from forwarder.tickets.events import Event, EventBus


def test_format_sse_keeps_hangul():
    event = Event(7, "message.created", "T-1", {"text": "통관 보류"}, "t")
    chunk = format_sse(event)
    assert chunk.startswith("id: 7\nevent: message.created\ndata: ")
    assert "통관 보류" in chunk and chunk.endswith("\n\n")
    assert json.loads(chunk.splitlines()[2][6:]) == {
        "seq": 7, "type": "message.created", "ticket_id": "T-1", "payload": {"text": "통관 보류"}, "at": "t",
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_event_stream_replays_then_follows_without_duplicate():
    bus = EventBus()
    for _ in range(3):
        bus.publish("x", "T-1", {})

    async def connected():
        return False

    stream = event_stream(bus, since=1, accept=lambda _: True, is_disconnected=connected, ping_seconds=0.01)
    try:
        assert (await anext(stream)).startswith("id: 2\n")
        bus.publish("x", "T-1", {})
        assert (await anext(stream)).startswith("id: 3\n")
        assert (await anext(stream)).startswith("id: 4\n")
        assert await asyncio.wait_for(anext(stream), 0.5) == ": ping\n\n"
    finally:
        await stream.aclose()


@pytest.mark.anyio
async def test_event_stream_exits_when_disconnected():
    bus = EventBus()
    bus.publish("x", "T-1", {})

    async def disconnected():
        return True

    stream = event_stream(bus, since=0, accept=lambda _: True, is_disconnected=disconnected, ping_seconds=0.01)
    assert [chunk async for chunk in stream] == []


def test_customer_visible_filters_drafts_and_other_tickets_but_accepts_reset():
    draft = Event(1, "message.created", "T-1", {"message": {"status": "draft"}}, "t")
    sent = Event(2, "message.created", "T-1", {"message": {"status": "sent"}}, "t")
    step = Event(3, "run.step", "T-1", {}, "t")
    other = Event(4, "message.created", "T-2", {"message": {"status": "sent"}}, "t")
    reset = Event(5, "demo.reset", None, {}, "t")
    assert [customer_visible(e, "T-1") for e in (draft, sent, step, other, reset)] == [
        False, True, False, False, True,
    ]
