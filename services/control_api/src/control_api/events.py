"""SSE hub for the console (IF-API-CONTROL GET /stream).

Route handlers are sync (FastAPI runs them in a threadpool), so ``publish`` hands the
event to the event loop with ``call_soon_threadsafe``. Each subscriber has a bounded
queue; a slow client loses its oldest events rather than stalling everyone.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

Event = dict[str, Any]


class EventHub:
    def __init__(self, queue_max: int) -> None:
        self._queue_max = queue_max
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_max)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(queue)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._fanout, {"type": type_, "data": data})

    def _fanout(self, event: Event) -> None:
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


def format_sse(event: Event) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


async def sse_stream(
    hub: EventHub,
    heartbeat_s: float,
    is_disconnected: Callable[[], Awaitable[bool]],
) -> AsyncIterator[bytes]:
    queue = hub.subscribe()
    try:
        yield b": connected\n\n"
        while not await is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat_s)
            except TimeoutError:
                yield b": heartbeat\n\n"
                continue
            yield format_sse(event)
    finally:
        hub.unsubscribe(queue)
