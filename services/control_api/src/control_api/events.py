"""SSE hub for the console (IF-API-CONTROL GET /stream).

Route handlers are sync (FastAPI runs them in a threadpool), so ``publish`` hands the
event to the event loop with ``call_soon_threadsafe``. Each subscriber has a bounded
queue; a slow client loses its oldest events rather than stalling everyone.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

Event = dict[str, Any]


class EventHub:
    def __init__(self, queue_max: int) -> None:
        self._queue_max = queue_max
        # queue -> the tenant that subscriber may see, or None for a platform user.
        self._subscribers: dict[asyncio.Queue[Event], str | None] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self, tenant: str | None = None) -> asyncio.Queue[Event]:
        """Subscribe. ``tenant`` pins the subscriber; None means every tenant (platform)."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_max)
        self._subscribers[queue] = tenant
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event]) -> None:
        self._subscribers.pop(queue, None)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._fanout, {"type": type_, "data": data})

    def _fanout(self, event: Event) -> None:
        for queue, tenant in list(self._subscribers.items()):
            if not _visible_to(event, tenant):
                continue
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


def _visible_to(event: Event, tenant: str | None) -> bool:
    """Whether a tenant-pinned subscriber may see this event.

    The hub used to send every event to every subscriber, so a tenant-scoped console received
    other tenants' drift, draft and source events. Payloads that name a tenant are filtered on
    it; the few that do not (a reset announcement) are platform-wide news and still go to all.
    """
    if tenant is None:
        return True
    data = event.get("data")
    owner = data.get("tenant_id") if isinstance(data, dict) else None
    return owner is None or owner == tenant


def format_sse(event: Event) -> bytes:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()


async def sse_stream(
    hub: EventHub,
    heartbeat_s: float,
    is_disconnected: Callable[[], Awaitable[bool]],
    tenant: str | None = None,
) -> AsyncGenerator[bytes, None]:
    queue = hub.subscribe(tenant)
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
