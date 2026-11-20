"""EventBus: numbered events from any thread, kept for replay and pushed to asyncio subscribers."""

import asyncio
import json
import threading
from collections import deque
from dataclasses import dataclass

from .models import now_iso


@dataclass(frozen=True)
class Event:
    """One published event. The same object goes to every reader, so readers must not change it."""

    seq: int  # 1, 2, 3, … in the order the events were published
    type: str  # e.g. "message.created"
    ticket_id: str | None  # None for an event about no single ticket
    payload: dict
    at: str  # when it was published


class EventBus:
    """publish() may be called from any thread; subscribers are asyncio queues fed on their own loops.

    The latest buffer_size events are kept for since(); older ones are gone.
    """

    def __init__(self, buffer_size: int = 1000) -> None:
        if buffer_size < 1:
            raise ValueError(f"buffer_size must be at least 1, not {buffer_size}")
        # Guards everything below. publish() delivers while holding it, so each subscriber gets the
        # events in seq order, and subscribe() falls cleanly before or after any one event.
        self._lock = threading.Lock()
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._last_seq = 0
        self._subscribers: dict[asyncio.Queue[Event], asyncio.AbstractEventLoop] = {}

    @property
    def last_seq(self) -> int:
        """The seq of the latest event; 0 before the first."""
        with self._lock:
            return self._last_seq

    def publish(self, type: str, ticket_id: str | None, payload: dict) -> Event:
        """Number the event, keep it, and queue it for every subscriber on the subscriber's loop.

        The event carries a JSON copy of payload: later changes to payload do not reach it, and a
        payload that is not JSON-serialisable raises here, taking no seq, rather than breaking every
        stream that would replay it. A subscriber whose loop has closed is dropped.
        """
        snapshot = json.loads(json.dumps(payload, ensure_ascii=False))
        with self._lock:
            self._last_seq += 1
            event = Event(self._last_seq, type, ticket_id, snapshot, now_iso())
            self._buffer.append(event)
            for queue, loop in list(self._subscribers.items()):
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
                except RuntimeError:  # the loop is closed, so nothing can read this queue any more
                    del self._subscribers[queue]
        return event

    def since(self, seq: int) -> list[Event]:
        """The kept events numbered above seq, oldest first."""
        with self._lock:
            return [event for event in self._buffer if event.seq > seq]

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[Event]:
        """A new queue that receives, on loop, every event published from now until unsubscribe().

        To replay and then follow without a gap, subscribe first, then call since(), and skip the
        queued events whose seq the replay already covered.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue()
        with self._lock:
            self._subscribers[queue] = loop
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        """Stop feeding queue. A queue that is not subscribed is ignored."""
        with self._lock:
            self._subscribers.pop(queue, None)
