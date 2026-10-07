"""The delivery loop: one worker per route, and offsets that move only when everyone is done.

The shape A6 asks for, and why each part is there:

* **A thread and a bounded queue per route.** One slow sink must not stall the others — that is
  what v1 got from a consumer group per route, and a bounded queue is the same isolation in one
  process. The bound matters: unbounded, a dead partner sink would grow until the router was
  OOM-killed, turning one route's outage into everyone's.
* **Offsets commit only after every route has flushed and receipted the batch.** That is what makes
  delivery at-least-once across a crash. Committing earlier would lose events that were consumed
  but never written; the price is that a restart can re-deliver, which a SIEM tolerates and an
  auditor can see (every delivery leaves a receipt).
* **A blocked route blocks the commit, not the consumer.** The loop keeps polling and buffering up
  to the queue bound, so Kafka does not see a stalled consumer and rebalance us away mid-outage.
* **A worker writes whatever is waiting in one go.** One write and one flush per event was the
  router's ceiling (about 110 events/s on a laptop, under the workstation profile's own baseline
  traffic), so a worker takes up to ``batch_max`` queued deliveries and flushes once for them all.

Receipts are produced for every outcome, including `filtered`: "why did this event not reach Wazuh?"
must be answerable from the receipts topic alone, without re-running the filters.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from router.routes import Route
from router.sinks import SinkError, SyslogTcpSink
from veyra_common.envelope import rfc3339_ns
from veyra_common.models import Receipt

log = logging.getLogger(__name__)

# How long a worker waits before retrying a retryable sink failure, when the sink has no opinion.
DEFAULT_RETRY_S = 1.0
# How many times a batch is retried before it is receipted `failed` and dropped. A permanently
# unwritable sink must not hold the commit forever: that would stop every other route too.
MAX_ATTEMPTS = 60


@dataclass(slots=True)
class Delivery:
    """One event on its way to one route."""

    event_uid: str
    revision: int
    payload: dict[str, Any]
    done: threading.Event = field(default_factory=threading.Event)
    status: str = "pending"
    detail: str = ""


class RouteWorker:
    """Owns one route's sink, its queue and its thread."""

    def __init__(
        self,
        route: Route,
        *,
        queue_max: int,
        on_receipt: Any,
        clock: Any = rfc3339_ns,
        batch_max: int = 1,
    ) -> None:
        self.route = route
        self.batch_max = max(1, batch_max)
        self.queue: queue.Queue[Delivery | None] = queue.Queue(maxsize=max(1, queue_max))
        self.on_receipt = on_receipt
        self.clock = clock
        self.delivered = 0
        self.failed = 0
        self.last_delivered_at = time.monotonic()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"route-{self.route.id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=15)
        self.route.sink.close()

    def submit(self, delivery: Delivery) -> None:
        """Hand a delivery to this route, blocking while its queue is full (backpressure)."""
        while not self._stop.is_set():
            try:
                self.queue.put(delivery, timeout=1.0)
                return
            except queue.Full:
                log.warning("route queue full", extra={"route": self.route.id})

    # ---------------------------------------------------------------- the loop
    def _run(self) -> None:
        while not self._stop.is_set():
            batch = self._take()
            if not batch:
                return
            self._deliver(batch)

    def _take(self) -> list[Delivery]:
        """The next delivery, plus whatever else is already waiting, up to ``batch_max``."""
        first = self.queue.get()
        if first is None:
            return []
        batch = [first]
        while len(batch) < self.batch_max:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            if item is None:  # stop() has set the flag; _deliver receipts these as not delivered
                break
            batch.append(item)
        return batch

    def _deliver(self, batch: list[Delivery]) -> None:
        payloads = [delivery.payload for delivery in batch]
        attempts = 0
        while not self._stop.is_set():
            attempts += 1
            try:
                self.route.sink.write(payloads)
                self.route.sink.flush()
            except SinkError as exc:
                if exc.permanent or attempts >= MAX_ATTEMPTS:
                    self._finish(batch, "failed", str(exc))
                    return
                wait = getattr(self.route.sink, "retry_after", DEFAULT_RETRY_S)
                log.warning(
                    "sink write failed, retrying",
                    extra={"route": self.route.id, "attempt": attempts, "error": str(exc)},
                )
                self._stop.wait(min(float(wait), 30.0))
                continue
            self._finish(batch, "delivered", "")
            return
        # Stopping with the batch unwritten: say so rather than reporting it delivered.
        self._finish(batch, "failed", "router stopped before delivery")

    def _finish(self, batch: list[Delivery], status: str, detail: str) -> None:
        if status == "delivered":
            self.delivered += len(batch)
            self.last_delivered_at = time.monotonic()
        else:
            self.failed += len(batch)
            log.error(
                "delivery failed",
                extra={"route": self.route.id, "events": len(batch), "detail": detail},
            )
        for delivery in batch:
            delivery.status = status
            delivery.detail = detail
            self.on_receipt(
                Receipt(
                    event_uid=delivery.event_uid,
                    revision=delivery.revision,
                    route_id=self.route.id,
                    status=status,  # type: ignore[arg-type]
                    detail=detail[:500],
                    at=self.clock(),
                )
            )
            delivery.done.set()

    # ---------------------------------------------------------------- observability
    @property
    def breaker_state(self) -> int:
        return self.route.sink.breaker_state

    @property
    def lag_seconds(self) -> float:
        """How long since this route last delivered — what an operator watches during an outage."""
        if self.queue.empty():
            return 0.0
        return time.monotonic() - self.last_delivered_at

    @property
    def is_remote(self) -> bool:
        return isinstance(self.route.sink, SyslogTcpSink)


class Dispatcher:
    """Fans one event out to every route and tells the caller when all of them are done."""

    def __init__(
        self, workers: list[RouteWorker], *, on_receipt: Any, clock: Any = rfc3339_ns
    ) -> None:
        self.workers = workers
        self.on_receipt = on_receipt
        self.clock = clock

    def dispatch(self, event: dict[str, Any]) -> list[Delivery]:
        """Submit the event to every accepting route; receipt the rest as ``filtered``."""
        ulpf = event.get("ulpf") or {}
        event_uid = str(ulpf.get("event_uid") or event.get("event_uid") or "")
        revision = int(ulpf.get("revision", 1))
        pending: list[Delivery] = []
        for worker in self.workers:
            if not worker.route.accepts(event):
                # A filtered event is recorded, not forgotten: "why is this not in Wazuh?" has to be
                # answerable from `receipts` alone.
                self.on_receipt(
                    Receipt(
                        event_uid=event_uid,
                        revision=revision,
                        route_id=worker.route.id,
                        status="filtered",
                        detail="",
                        at=self.clock(),
                    )
                )
                continue
            delivery = Delivery(
                event_uid=event_uid, revision=revision, payload=worker.route.payload(event)
            )
            pending.append(delivery)
            worker.submit(delivery)
        return pending

    @staticmethod
    def wait(deliveries: list[Delivery], timeout: float | None = None) -> bool:
        """True when every delivery finished (delivered or failed). Offsets wait on this."""
        deadline = None if timeout is None else time.monotonic() + timeout
        for delivery in deliveries:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not delivery.done.wait(timeout=remaining):
                return False
        return True
