import asyncio
import sys
import threading
from datetime import datetime, timedelta

import pytest

from forwarder.tickets.events import Event, EventBus
from forwarder.tickets.executor import InlineExecutor, ManualExecutor, ThreadExecutor


def test_bus_since_and_threadsafe_delivery():
    bus = EventBus()
    async def main():
        q = bus.subscribe(asyncio.get_running_loop())
        threading.Thread(target=lambda: bus.publish("x", "T-1", {"k": "한글"})).start()
        ev = await asyncio.wait_for(q.get(), 2)
        return ev
    ev = asyncio.run(main())
    assert ev.seq == 1 and ev.payload["k"] == "한글" and [e.seq for e in bus.since(0)] == [1]


def test_manual_executor_queues():
    ex = ManualExecutor(); out = []
    ex.submit(lambda: out.append(1))
    assert out == [] and ex.pending == 1 and ex.run_next() and out == [1]


def test_events_are_numbered_from_one_and_replayed_after_a_seq():
    bus = EventBus()
    assert bus.last_seq == 0 and bus.since(0) == []
    first = bus.publish("ticket.updated", "T-001040", {"ticket": {"id": "T-001040", "subject": "통관 문의"}})
    second = bus.publish("demo.reset", None, {})
    assert first == Event(1, "ticket.updated", "T-001040", {"ticket": {"id": "T-001040", "subject": "통관 문의"}}, first.at)
    assert second == Event(2, "demo.reset", None, {}, second.at)
    assert datetime.fromisoformat(first.at).utcoffset() == timedelta(hours=9)
    assert bus.last_seq == 2
    assert bus.since(0) == [first, second] and bus.since(1) == [second] and bus.since(2) == []


def test_replay_reaches_back_only_buffer_size_events():
    bus = EventBus(buffer_size=3)
    for i in range(5):
        bus.publish("x", None, {"i": i})
    assert [e.seq for e in bus.since(0)] == [3, 4, 5] and bus.last_seq == 5
    with pytest.raises(ValueError):
        EventBus(buffer_size=0)


def test_event_keeps_a_json_copy_of_the_payload():
    bus = EventBus()
    payload = {"message": {"text": "已收到", "tags": ("ack",)}}
    event = bus.publish("message.created", "T-1", payload)
    payload["message"]["text"] = "changed"
    assert event.payload == {"message": {"text": "已收到", "tags": ["ack"]}}
    # A payload that could never be streamed is refused at once and takes no seq.
    with pytest.raises(TypeError):
        bus.publish("x", "T-1", {"when": object()})
    assert bus.last_seq == 1 and bus.since(0) == [event]


@pytest.fixture
def frequent_thread_switches():
    """Switch threads as often as the interpreter can, so concurrent publishers really interleave."""
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    yield
    sys.setswitchinterval(previous)


def test_subscribers_get_events_from_many_threads_in_seq_order(frequent_thread_switches):
    bus = EventBus()
    publishers = [
        threading.Thread(target=lambda: [bus.publish("x", None, {}) for _ in range(100)]) for _ in range(8)
    ]

    async def main():
        queue = bus.subscribe(asyncio.get_running_loop())
        for publisher in publishers:
            publisher.start()
        return [(await asyncio.wait_for(queue.get(), 2)).seq for _ in range(800)]

    seqs = asyncio.run(main())
    for publisher in publishers:
        publisher.join()
    assert seqs == list(range(1, 801)) and bus.last_seq == 800


def test_unsubscribed_queue_gets_nothing_more():
    bus = EventBus()

    async def main():
        loop = asyncio.get_running_loop()
        kept, dropped = bus.subscribe(loop), bus.subscribe(loop)
        bus.unsubscribe(dropped)
        bus.unsubscribe(dropped)  # a second time is harmless
        publisher = threading.Thread(target=bus.publish, args=("x", None, {}))
        publisher.start()
        publisher.join()
        event = await asyncio.wait_for(kept.get(), 2)
        # Both queues' deliveries would have run before kept.get() returned.
        return event.seq, dropped.qsize()

    assert asyncio.run(main()) == (1, 0)


def test_publish_survives_subscribers_whose_loop_has_closed():
    bus = EventBus()
    closed = asyncio.new_event_loop()
    bus.subscribe(closed)
    closed.close()

    async def main():
        queue = bus.subscribe(asyncio.get_running_loop())
        bus.publish("x", None, {})
        return (await asyncio.wait_for(queue.get(), 2)).seq

    assert asyncio.run(main()) == 1
    # Now both subscribers' loops are closed.
    assert bus.publish("y", None, {}).seq == 2 and [e.seq for e in bus.since(0)] == [1, 2]


def test_inline_executor_runs_at_once_in_the_callers_thread():
    ran_on = []
    InlineExecutor().submit(lambda: ran_on.append(threading.current_thread()))
    assert ran_on == [threading.current_thread()]


def test_manual_executor_runs_in_order_and_takes_work_submitted_while_running():
    ex = ManualExecutor(); out = []
    ex.submit(lambda: (out.append("a"), ex.submit(lambda: out.append("c"))))
    ex.submit(lambda: out.append("b"))
    assert ex.run_next() and out == ["a"] and ex.pending == 2
    assert ex.run_next() and ex.run_next() and out == ["a", "b", "c"]
    assert ex.run_next() is False and ex.pending == 0


def test_manual_executor_drops_a_function_that_raised():
    ex = ManualExecutor()
    ex.submit(lambda: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        ex.run_next()
    assert ex.pending == 0


def test_thread_executor_runs_in_the_background_and_logs_failures(caplog):
    ex = ThreadExecutor(workers=1)
    ran_on = []
    done = threading.Event()

    def fail():
        raise RuntimeError("boom in a run")

    def work():
        ran_on.append(threading.current_thread())
        done.set()

    ex.submit(fail)
    ex.submit(work)  # the one worker carries on after the failure
    assert done.wait(2)
    ex.shutdown()
    assert ran_on[0] is not threading.current_thread()
    failures = [record for record in caplog.records if record.exc_info]
    assert len(failures) == 1 and str(failures[0].exc_info[1]) == "boom in a run"
