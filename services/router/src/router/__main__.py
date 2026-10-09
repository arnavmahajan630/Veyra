"""router — `norm.*` to Wazuh and the partner sink (A6). `python -m router`.

    python -m router

Consumes `^norm\\..*` — never `shadow`, which is a candidate-vs-active comparison, not an event.
Each event is rendered and masked per IF-ROUTES, delivered to every matching sink, and receipted
(IF-RECEIPT) per route. The **offset commits only once every route has flushed**, so a restart can
re-deliver but never skip, and every delivery — including a filtered one — leaves a receipt.

Events are taken up to `VEYRA_ROUTE_BATCH_MAX` at a time (waiting at most `VEYRA_ROUTE_BATCH_MS`
for more), and the offsets are committed once for the batch, after every route has flushed all
of it. One synchronous commit per event had the router slower than the traffic it was routing.

Routes come from the compacted `control` topic (key `routes`, published by control-api), with
`services/router/routes.default.yaml` as the fallback so the router works on a fresh stack. A route
set that fails to compile is rejected whole and the previous one keeps serving.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from typing import Any

from confluent_kafka import TopicPartition
from prometheus_client import Counter, Gauge

from router.delivery import Delivery, Dispatcher, RouteWorker
from router.keys import ensure_route_key
from router.routes import Route, compile_routes, load_routes_file
from router.settings import RouterSettings
from veyra_common.control import ControlReader
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import KEY_ROUTES, Receipt, RoutesMessage
from veyra_common.service import ServiceApp
from veyra_common.topics import NORM_PATTERN, TOPIC_RECEIPTS

log = logging.getLogger(__name__)

EVENTS = Counter("veyra_route_events_total", "Events per route and outcome", ["route", "status"])
LAG = Gauge(
    "veyra_route_lag_seconds", "Seconds since a route last delivered while backlogged", ["route"]
)
BREAKER = Gauge("veyra_route_breaker_state", "0 closed, 1 open, 2 half-open", ["route"])
ROUTES_LOADED = Gauge("veyra_routes_loaded", "Routes currently serving")
DROPPED = Counter("veyra_route_undeliverable_total", "Events no route accepted")


class Router:
    """Owns the route set, the workers and the receipt producer."""

    def __init__(self, cfg: RouterSettings, *, producer: Any = None) -> None:
        self.cfg = cfg
        self.master_key = ensure_route_key(cfg.keys_dir)
        self.producer = producer if producer is not None else make_producer(cfg=cfg)
        self.workers: list[RouteWorker] = []
        self.dispatcher = Dispatcher([], on_receipt=self.emit_receipt)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- routes
    def load(self, specs: list[Any]) -> list[str]:
        """Swap the route set. Returns the errors; the old set keeps serving if there are any."""
        routes, errors = compile_routes(specs, master_key=self.master_key, cfg=self.cfg)
        if errors:
            log.error("routes rejected", extra={"errors": errors[:5]})
            if not self.workers:
                # Nothing was serving yet, so serve whatever did compile rather than nothing at all.
                self._install(routes)
            return errors
        self._install(routes)
        return []

    def _install(self, routes: list[Route]) -> None:
        with self._lock:
            old = self.workers
            workers = [
                RouteWorker(
                    route,
                    queue_max=self.cfg.route_queue_max,
                    on_receipt=self.emit_receipt,
                    batch_max=self.cfg.route_batch_max,
                )
                for route in routes
            ]
            for worker in workers:
                worker.start()
            self.workers = workers
            self.dispatcher = Dispatcher(workers, on_receipt=self.emit_receipt)
        for worker in old:
            worker.stop()
        ROUTES_LOADED.set(len(routes))
        log.info("routes installed", extra={"routes": [route.id for route in routes]})

    # ---------------------------------------------------------------- receipts
    def emit_receipt(self, receipt: Receipt) -> None:
        EVENTS.labels(receipt.route_id, receipt.status).inc()
        self.producer.produce(
            TOPIC_RECEIPTS, key=receipt.event_uid, value=receipt.model_dump_json().encode()
        )

    def observe(self) -> None:
        for worker in self.workers:
            LAG.labels(worker.route.id).set(worker.lag_seconds)
            BREAKER.labels(worker.route.id).set(worker.breaker_state)

    # ---------------------------------------------------------------- one record
    def submit(self, value: bytes) -> list[Delivery] | None:
        """Hand one `norm.*` value to every route it belongs to. None when it cannot be parsed."""
        try:
            event = json.loads(value)
        except (json.JSONDecodeError, TypeError) as exc:
            log.error("norm event is not JSON", extra={"error": str(exc)})
            return None
        if not isinstance(event, dict):
            return None
        deliveries = self.dispatcher.dispatch(event)
        if not deliveries:
            DROPPED.inc()
        return deliveries

    def handle(self, value: bytes) -> bool:
        """Deliver one `norm.*` value everywhere it belongs. False when it could not be parsed."""
        deliveries = self.submit(value)
        if deliveries is None:
            return False
        self.dispatcher.wait(deliveries)
        return True

    def handle_batch(self, messages: list[Any]) -> list[TopicPartition]:
        """Deliver a batch of consumed messages; return the positions now safe to commit.

        They are safe once every route has flushed every event of the batch. A value that could
        not be parsed does not move its partition's position, as when events came one at a time.
        """
        pending: list[Delivery] = []
        reached: dict[tuple[str, int], int] = {}
        for message in messages:
            if message.error():
                log.error("consume error", extra={"error": str(message.error())})
                continue
            deliveries = self.submit(message.value())
            if deliveries is None:
                continue
            pending.extend(deliveries)
            key = (message.topic(), message.partition())
            reached[key] = max(reached.get(key, -1), message.offset())
        self.dispatcher.wait(pending)
        return [
            TopicPartition(topic, partition, offset + 1)
            for (topic, partition), offset in reached.items()
        ]

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()
        self.producer.flush(15)


def main() -> None:
    cfg = RouterSettings()
    app = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg)
    router = Router(cfg)
    app.on_stop(router.stop)

    # The fallback first, so the router is already delivering while `control` is being read.
    try:
        router.load(load_routes_file(cfg.routes_file))
    except Exception as exc:
        log.error(
            "no usable fallback routes", extra={"path": str(cfg.routes_file), "error": str(exc)}
        )

    state: dict[str, Any] = {}

    def on_message(key: str, payload: dict[str, Any] | None) -> None:
        if key != KEY_ROUTES:
            return
        state["routes"] = None if payload is None else RoutesMessage.model_validate(payload).routes

    def on_change() -> None:
        specs = state.get("routes")
        if not specs:
            return
        # Install only a document that actually differs. Reinstalling swaps every route's worker and
        # sink, which aborts whatever was in flight — not something to do because a message arrived.
        fingerprint = [spec.model_dump(mode="json") for spec in specs]
        if fingerprint == state.get("installed"):
            return
        state["installed"] = fingerprint
        router.load(list(specs))

    reader = ControlReader(
        group=f"{cfg.consumer_group}-control-{cfg.instance}",
        cfg=cfg,
        prefixes=(KEY_ROUTES,),
        on_message=on_message,
        on_change=on_change,
        name="router-control",
    )
    reader.start()
    app.on_stop(reader.stop)
    if not reader.wait_ready(timeout=60):
        log.error("control topic did not settle in 60s; serving the fallback routes")
    app.mark_ready()

    consumer = make_consumer(cfg.consumer_group, pattern=NORM_PATTERN, cfg=cfg)
    stopping = threading.Event()

    def stop(*_: Any) -> None:
        stopping.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    app.on_stop(stopping.set)

    log.info("router running", extra={"group": cfg.consumer_group})
    try:
        while not stopping.is_set():
            first = consumer.poll(1.0)
            router.observe()
            if first is None:
                continue
            batch = [first]
            if cfg.route_batch_max > 1:
                batch += consumer.consume(
                    cfg.route_batch_max - 1, timeout=cfg.route_batch_ms / 1000
                )
            positions = router.handle_batch(batch)
            if positions:
                # Every route has flushed and receipted the batch, so these are safe to lose.
                consumer.commit(offsets=positions, asynchronous=False)
            router.producer.poll(0)
    finally:
        consumer.close()
        router.stop()
        app.stop()


if __name__ == "__main__":
    main()
