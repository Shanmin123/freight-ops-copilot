"""Serialize events and replay them before switching to live delivery."""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict

from forwarder.tickets.events import Event, EventBus


def format_sse(event: Event) -> str:
    data = json.dumps(asdict(event), ensure_ascii=False)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {data}\n\n"


def customer_visible(event: Event, ticket_id: str) -> bool:
    if event.type == "demo.reset":
        return True
    if event.ticket_id != ticket_id:
        return False
    return event.type == "ticket.typing" or (
        event.type in ("message.created", "message.updated")
        and event.payload.get("message", {}).get("status") == "sent"
    )


async def event_stream(
    bus: EventBus,
    *,
    since: int,
    accept: Callable[[Event], bool],
    is_disconnected: Callable[[], Awaitable[bool]],
    ping_seconds: float,
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    # Subscribe first. An event published during the handoff can then appear in both sources.
    queue = bus.subscribe(loop)
    try:
        cursor = since
        for event in bus.since(since):
            cursor = event.seq
            if accept(event):
                if await is_disconnected():
                    return
                yield format_sse(event)

        deadline = loop.time() + ping_seconds
        while not await is_disconnected():
            remaining = deadline - loop.time()
            if remaining <= 0:
                yield ": ping\n\n"
                deadline = loop.time() + ping_seconds
                continue
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            if event.seq > cursor and accept(event):
                if await is_disconnected():
                    return
                yield format_sse(event)
                deadline = loop.time() + ping_seconds
    finally:
        bus.unsubscribe(queue)
