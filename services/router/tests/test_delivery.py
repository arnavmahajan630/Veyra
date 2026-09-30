"""Delivery: receipts for every outcome, and the offset rule that makes at-least-once true.

The property under test is the one an auditor cares about: **an offset must not move until every
route has flushed.** If it did, a crash would lose events that were consumed and never written, and
nothing downstream would ever know they existed — no receipt, no gap, no way to notice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from router.delivery import Delivery, Dispatcher, RouteWorker
from router.routes import compile_route
from router.settings import RouterSettings
from router.sinks import SinkError
from router_helpers import FakeProducer, norm_event

from veyra_common.models import Receipt


def route_for(
    cfg: RouterSettings, tmp_path: Path, *, route_id: str = "wazuh_main", **overrides: Any
) -> Any:
    spec: dict[str, Any] = {
        "id": route_id,
        "filter": {},
        "format": "ocsf_json",
        "masking": "none",
        "sink": {"type": "ndjson_file", "path": str(tmp_path / f"{route_id}.ndjson")},
    }
    spec.update(overrides)
    return compile_route(spec, master_key=b"k" * 32, cfg=cfg)


def receipts_of(collected: list[Receipt]) -> list[tuple[str, str]]:
    return [(receipt.route_id, receipt.status) for receipt in collected]


def worker_for(route: Any, collected: list[Receipt], *, queue_max: int = 10) -> RouteWorker:
    return RouteWorker(route, queue_max=queue_max, on_receipt=collected.append)


# ---------------------------------------------------------------- one route
def test_a_delivered_event_reaches_the_sink_and_leaves_a_receipt(
    cfg: RouterSettings, tmp_path: Path
) -> None:
    collected: list[Receipt] = []
    route = route_for(cfg, tmp_path)
    worker = worker_for(route, collected)
    worker.start()
    try:
        dispatcher = Dispatcher([worker], on_receipt=collected.append)
        deliveries = dispatcher.dispatch(norm_event())
        assert dispatcher.wait(deliveries, timeout=10)
    finally:
        worker.stop()

    line = json.loads((tmp_path / "wazuh_main.ndjson").read_text().splitlines()[0])
    assert line["veyra"]["tier"] == 1
    assert receipts_of(collected) == [("wazuh_main", "delivered")]
    assert collected[0].event_uid == "0192a4f0-0000-7000-8000-000000000001"
    assert collected[0].revision == 1


def test_a_filtered_event_is_receipted_and_never_written(
    cfg: RouterSettings, tmp_path: Path
) -> None:
    """ "Why is this not in Wazuh?" must be answerable from `receipts` alone."""
    collected: list[Receipt] = []
    route = route_for(cfg, tmp_path, route_id="partner", filter={"tenants": ["t_ntro_core"]})
    worker = worker_for(route, collected)
    worker.start()
    try:
        dispatcher = Dispatcher([worker], on_receipt=collected.append)
        deliveries = dispatcher.dispatch(norm_event(tenant="t_maha_power"))
        assert deliveries == []
    finally:
        worker.stop()

    assert receipts_of(collected) == [("partner", "filtered")]
    assert not (tmp_path / "partner.ndjson").exists()


def test_a_permanent_failure_is_receipted_failed(cfg: RouterSettings, tmp_path: Path) -> None:
    collected: list[Receipt] = []
    route = route_for(cfg, tmp_path)

    class Broken:
        breaker_state = 0

        def write(self, payloads: list[dict[str, Any]]) -> None:
            raise SinkError("disk is read-only", permanent=True)

        def flush(self) -> None: ...
        def close(self) -> None: ...

    route.sink.close()
    route.sink = Broken()  # type: ignore[assignment]
    worker = worker_for(route, collected)
    worker.start()
    try:
        dispatcher = Dispatcher([worker], on_receipt=collected.append)
        assert dispatcher.wait(dispatcher.dispatch(norm_event()), timeout=10)
    finally:
        worker.stop()

    assert receipts_of(collected) == [("wazuh_main", "failed")]
    assert "read-only" in collected[0].detail
    assert worker.failed == 1


def test_a_retryable_failure_is_retried_until_it_succeeds(
    cfg: RouterSettings, tmp_path: Path
) -> None:
    """A SIEM that comes back must get the events it missed — that is AC4 in miniature."""
    collected: list[Receipt] = []
    route = route_for(cfg, tmp_path)

    class FlakyThenFine:
        breaker_state = 0
        retry_after = 0.01

        def __init__(self) -> None:
            self.attempts = 0
            self.written: list[dict[str, Any]] = []

        def write(self, payloads: list[dict[str, Any]]) -> None:
            self.attempts += 1
            if self.attempts < 3:
                raise SinkError("connection refused")
            self.written.extend(payloads)

        def flush(self) -> None: ...
        def close(self) -> None: ...

    sink = FlakyThenFine()
    route.sink.close()
    route.sink = sink  # type: ignore[assignment]
    worker = worker_for(route, collected)
    worker.start()
    try:
        dispatcher = Dispatcher([worker], on_receipt=collected.append)
        assert dispatcher.wait(dispatcher.dispatch(norm_event()), timeout=10)
    finally:
        worker.stop()

    assert sink.attempts == 3
    assert len(sink.written) == 1
    assert receipts_of(collected) == [("wazuh_main", "delivered")]


# ---------------------------------------------------------------- fan-out
def test_both_routes_get_the_event_and_each_one_receipts(
    cfg: RouterSettings, tmp_path: Path
) -> None:
    collected: list[Receipt] = []
    wazuh = worker_for(route_for(cfg, tmp_path), collected)
    partner = worker_for(
        route_for(
            cfg,
            tmp_path,
            route_id="partner_masked",
            filter={"tenants": ["t_maha_power"], "classes": [3002]},
            masking={"user.name": "hmac", "raw_data": "redact"},
        ),
        collected,
    )
    for worker in (wazuh, partner):
        worker.start()
    try:
        dispatcher = Dispatcher([wazuh, partner], on_receipt=collected.append)
        assert dispatcher.wait(dispatcher.dispatch(norm_event()), timeout=10)
    finally:
        for worker in (wazuh, partner):
            worker.stop()

    assert sorted(receipts_of(collected)) == [
        ("partner_masked", "delivered"),
        ("wazuh_main", "delivered"),
    ]
    # The same event, two different renderings — the point of per-route masking.
    to_wazuh = json.loads((tmp_path / "wazuh_main.ndjson").read_text().splitlines()[0])
    to_partner = json.loads((tmp_path / "partner_masked.ndjson").read_text().splitlines()[0])
    assert to_wazuh["user"]["name"] == "a.sharma"
    assert to_partner["user"]["name"].startswith("h_")
    assert to_partner["raw_data"] == "[REDACTED]"


def test_an_event_no_route_accepts_is_still_accounted_for(
    cfg: RouterSettings, tmp_path: Path
) -> None:
    collected: list[Receipt] = []
    worker = worker_for(route_for(cfg, tmp_path, filter={"tiers": [1]}), collected)
    worker.start()
    try:
        dispatcher = Dispatcher([worker], on_receipt=collected.append)
        assert dispatcher.dispatch(norm_event(tier=4)) == []
    finally:
        worker.stop()
    assert receipts_of(collected) == [("wazuh_main", "filtered")]


# ---------------------------------------------------------------- the offset rule
def test_wait_is_false_while_a_route_is_still_working() -> None:
    """This is what holds the offset back: an unfinished delivery means an uncommitted record."""
    pending = Delivery(event_uid="u", revision=1, payload={})
    assert not Dispatcher.wait([pending], timeout=0.05)
    pending.done.set()
    assert Dispatcher.wait([pending], timeout=0.05)


def test_wait_returns_true_for_nothing_to_do() -> None:
    assert Dispatcher.wait([], timeout=0)


def test_a_slow_route_does_not_stop_a_fast_one(cfg: RouterSettings, tmp_path: Path) -> None:
    """Bounded queue per route: the fast sink keeps delivering while the slow one is stuck."""
    collected: list[Receipt] = []
    fast = worker_for(route_for(cfg, tmp_path, route_id="fast"), collected)

    class Slow:
        breaker_state = 0
        retry_after = 0.05

        def __init__(self) -> None:
            self.release = False

        def write(self, payloads: list[dict[str, Any]]) -> None:
            if not self.release:
                raise SinkError("not yet")

        def flush(self) -> None: ...
        def close(self) -> None: ...

    slow_route = route_for(cfg, tmp_path, route_id="slow")
    slow_route.sink.close()
    slow_sink = Slow()
    slow_route.sink = slow_sink  # type: ignore[assignment]
    slow = worker_for(slow_route, collected)
    for worker in (fast, slow):
        worker.start()
    try:
        dispatcher = Dispatcher([fast, slow], on_receipt=collected.append)
        deliveries = dispatcher.dispatch(norm_event())
        # The batch as a whole is not done — so the offset is not committed…
        assert not dispatcher.wait(deliveries, timeout=0.3)
        # …but the fast route has already written its line.
        assert (tmp_path / "fast.ndjson").exists()
        assert ("fast", "delivered") in receipts_of(collected)
        slow_sink.release = True
        assert dispatcher.wait(deliveries, timeout=10)
    finally:
        for worker in (fast, slow):
            worker.stop()
    assert ("slow", "delivered") in receipts_of(collected)


def test_receipts_are_produced_to_the_receipts_topic(
    cfg: RouterSettings, tmp_path: Path, producer: FakeProducer
) -> None:
    """The service-level wiring: a Receipt becomes a record on `receipts`, keyed by event_uid."""
    from router.__main__ import Router

    cfg.routes_file = Path("services/router/routes.default.yaml")
    router = Router(cfg, producer=producer)
    router.load(
        [
            {
                "id": "wazuh_main",
                "filter": {},
                "sink": {"type": "ndjson_file", "path": str(tmp_path / "w.ndjson")},
            }
        ]
    )
    try:
        assert router.handle(json.dumps(norm_event()).encode())
    finally:
        router.stop()

    receipts = producer.receipts()
    assert len(receipts) == 1
    assert receipts[0]["route_id"] == "wazuh_main"
    assert receipts[0]["status"] == "delivered"
    assert producer.messages[0][1] == "0192a4f0-0000-7000-8000-000000000001"


def test_a_malformed_norm_value_is_not_committed(
    cfg: RouterSettings, tmp_path: Path, producer: FakeProducer
) -> None:
    """`handle` returning False is what keeps the offset where it is."""
    from router.__main__ import Router

    router = Router(cfg, producer=producer)
    router.load(
        [
            {
                "id": "wazuh_main",
                "filter": {},
                "sink": {"type": "ndjson_file", "path": str(tmp_path / "w.ndjson")},
            }
        ]
    )
    try:
        assert not router.handle(b"{not json")
        assert not router.handle(b'"a string, not an event"')
    finally:
        router.stop()
    assert producer.receipts() == []
