"""Executors that run the ticket service's work: at once, on background threads, or when a test says so."""

import logging
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

logger = logging.getLogger(__name__)


class Executor(Protocol):
    """What the ticket service hands its agent runs to; any of the three classes below will do."""

    def submit(self, fn: Callable[[], object]) -> None:
        """Run fn some time after this call, or during it; its return value is ignored."""


class InlineExecutor:
    """Runs fn at once in the caller's thread; whatever fn raises reaches the caller."""

    def submit(self, fn: Callable[[], object]) -> None:
        fn()


class ThreadExecutor:
    """Runs each fn on a pool of background threads. An exception from fn is logged, not raised."""

    def __init__(self, workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ticket-worker")

    def submit(self, fn: Callable[[], object]) -> None:
        self._pool.submit(_run_logging_errors, fn)

    def shutdown(self) -> None:
        """Wait until every submitted fn has run. submit() raises RuntimeError afterwards."""
        self._pool.shutdown(wait=True)


class ManualExecutor:
    """Queues each fn until run_next() runs it in the caller's thread, oldest first. For tests."""

    def __init__(self) -> None:
        self._queue: deque[Callable[[], object]] = deque()

    @property
    def pending(self) -> int:
        """How many submitted functions have not been run yet."""
        return len(self._queue)

    def submit(self, fn: Callable[[], object]) -> None:
        self._queue.append(fn)

    def run_next(self) -> bool:
        """Run the oldest queued fn and return True, or return False if none is queued.

        fn leaves the queue before it runs, so it may submit more work, and if it raises, the
        exception reaches the caller and fn is not run again.
        """
        if not self._queue:
            return False
        fn = self._queue.popleft()
        fn()
        return True


def _run_logging_errors(fn: Callable[[], object]) -> None:
    # A pool thread would keep the exception in a Future that nobody reads.
    try:
        fn()
    except Exception:
        logger.exception("background work %r failed", fn)
