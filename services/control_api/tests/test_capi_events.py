"""The SSE hub: thread-safe fan-out, bounded queues, heartbeats, clean unsubscribe."""

from __future__ import annotations

import asyncio

from control_api.events import EventHub, sse_stream


async def test_publish_from_a_thread_reaches_every_subscriber() -> None:
    hub = EventHub(queue_max=8)
    hub.bind(asyncio.get_running_loop())
    first, second = hub.subscribe(), hub.subscribe()
    await asyncio.to_thread(hub.publish, "source", {"id": "src_a"})
    expected = {"type": "source", "data": {"id": "src_a"}}
    assert await asyncio.wait_for(first.get(), 1) == expected
    assert await asyncio.wait_for(second.get(), 1) == expected


def test_publish_before_bind_is_a_noop() -> None:
    EventHub(queue_max=8).publish("source", {})


async def test_a_slow_subscriber_drops_its_oldest_events() -> None:
    hub = EventHub(queue_max=2)
    hub.bind(asyncio.get_running_loop())
    queue = hub.subscribe()
    for n in range(3):
        hub.publish("drift", {"n": n})
    await asyncio.sleep(0)
    assert [queue.get_nowait()["data"]["n"] for _ in range(2)] == [1, 2]


async def test_sse_stream_emits_events_and_heartbeats_then_unsubscribes() -> None:
    hub = EventHub(queue_max=8)
    hub.bind(asyncio.get_running_loop())

    async def never_disconnected() -> bool:
        return False

    stream = sse_stream(hub, heartbeat_s=0.05, is_disconnected=never_disconnected)
    assert await anext(stream) == b": connected\n\n"
    hub.publish("contract", {"id": "c1"})
    assert await asyncio.wait_for(anext(stream), 1) == (
        b'event: contract\ndata: {"type": "contract", "data": {"id": "c1"}}\n\n'
    )
    assert await asyncio.wait_for(anext(stream), 1) == b": heartbeat\n\n"
    await stream.aclose()  # type: ignore[attr-defined]  # AsyncIterator lacks aclose; runtime is a generator
    assert hub.subscriber_count == 0
