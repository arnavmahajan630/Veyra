"""Never 200 on a maybe, and never let one source drown the others.

These two are the properties that decide whether an HTTP ingest path can be trusted: a client that
gets a 200 must be free to delete its copy, and a quota must actually bound a sender.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from gateway_helpers import KEY_ID, SOURCE_ID, apikey_message
from ingest_gateway.app import GatewayContext
from ingest_gateway.producer import DeliveryFailed, RawProducer
from ingest_gateway.quota import QuotaLimiter
from ingest_gateway.registry import KeyRegistry
from ingest_gateway.settings import GatewaySettings


# ---------------------------------------------------------------- durability
def test_a_delivery_error_is_503_not_200(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    fake.fail_delivery = "Broker: Not enough in-sync replicas"
    response = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert response.status_code == 503
    assert "in-sync" in response.json()["text"]


def test_an_unflushed_message_is_503(client: TestClient, fake: Any, auth: dict[str, str]) -> None:
    """flush() returning non-zero means the broker never answered inside the ack timeout."""
    fake.leave_unflushed = 1
    response = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert response.status_code == 503
    assert "not acknowledged" in response.json()["text"]


def test_a_full_producer_queue_is_503(client: TestClient, fake: Any, auth: dict[str, str]) -> None:
    fake.raise_buffer_error = True
    response = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert response.status_code == 503
    assert "queue full" in response.json()["text"]


def test_a_partial_batch_is_never_acknowledged(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    """Three events, one delivery error: the client must be told the whole request failed."""
    fake.fail_delivery = "Broker: Message timed out"
    response = client.post(
        "/services/collector/event",
        content='{"event":"one"}{"event":"two"}{"event":"three"}',
        headers=auth,
    )
    assert response.status_code == 503
    assert "ackId" not in response.text


def test_send_all_of_nothing_is_not_an_error(cfg: GatewaySettings) -> None:
    producer = RawProducer(cfg, producer=_NullProducer())
    assert producer.send_all([]) == 0


class _NullProducer:
    def produce(self, *_: Any, **__: Any) -> None:  # pragma: no cover - never called
        raise AssertionError("nothing should be produced")

    def flush(self, timeout: float = 10) -> int:  # pragma: no cover
        return 0


def test_delivery_failed_names_the_first_errors(cfg: GatewaySettings) -> None:
    """The 503 body is read by a human under demo pressure; it has to say what broke."""

    class Failing:
        def produce(self, *_: Any, on_delivery: Any = None, **__: Any) -> None:
            on_delivery("Broker: down", None)

        def flush(self, timeout: float = 10) -> int:
            return 0

    from veyra_common.envelope import stamp

    envelope = stamp(b"x", collector_id="t", transport="http_hec_event", framing_method="http_body")
    with pytest.raises(DeliveryFailed, match="Broker: down"):
        RawProducer(cfg, producer=Failing()).send_all([envelope])


# ---------------------------------------------------------------- quotas
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_the_bucket_holds_one_second_of_events() -> None:
    clock = FakeClock()
    limiter = QuotaLimiter(clock=clock)
    assert all(limiter.allow("s", 5) for _ in range(5))
    assert not limiter.allow("s", 5), "the 6th event in the same instant is over quota"


def test_the_bucket_refills_at_the_quota_rate() -> None:
    clock = FakeClock()
    limiter = QuotaLimiter(clock=clock)
    for _ in range(5):
        limiter.allow("s", 5)
    clock.advance(0.4)  # 0.4 s at 5 EPS = 2 tokens
    assert limiter.allow("s", 5, cost=2)
    assert not limiter.allow("s", 5)


def test_the_bucket_never_exceeds_its_capacity() -> None:
    """An idle source must not bank a minute's worth and then burst it."""
    clock = FakeClock()
    limiter = QuotaLimiter(clock=clock)
    clock.advance(600)
    assert limiter.allow("s", 5, cost=5)
    assert not limiter.allow("s", 5)


def test_a_request_larger_than_the_bucket_is_allowed_once_full() -> None:
    """Otherwise a 40-line batch against a 5 EPS quota could never be sent at all."""
    clock = FakeClock()
    limiter = QuotaLimiter(clock=clock)
    assert limiter.allow("s", 5, cost=40)
    assert not limiter.allow("s", 5), "and it drains the bucket"


def test_quotas_are_per_source() -> None:
    limiter = QuotaLimiter(clock=FakeClock())
    for _ in range(5):
        limiter.allow("busy", 5)
    assert limiter.allow("quiet", 5), "one noisy source must not throttle another"


def test_a_requota_rebuilds_the_bucket() -> None:
    clock = FakeClock()
    limiter = QuotaLimiter(clock=clock)
    for _ in range(5):
        limiter.allow("s", 5)
    assert limiter.allow("s", 50), "a re-issued key with a bigger quota takes effect at once"


def test_zero_means_unlimited() -> None:
    limiter = QuotaLimiter(clock=FakeClock())
    assert all(limiter.allow("s", 0) for _ in range(1000))


def test_over_quota_is_429_with_retry_after(
    ctx: GatewayContext, registry: KeyRegistry, auth: dict[str, str], fake: Any
) -> None:
    from fastapi.testclient import TestClient
    from ingest_gateway.app import create_app

    registry._handle(f"apikey:{KEY_ID}", apikey_message(quota_eps=2))
    client = TestClient(create_app(ctx))
    codes = [
        client.post("/services/collector/event", content='{"event":"x"}', headers=auth).status_code
        for _ in range(6)
    ]
    assert codes[:2] == [200, 200]
    assert 429 in codes
    throttled = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert throttled.status_code == 429
    assert throttled.headers["Retry-After"] == "1"
    assert len(fake.messages) == 2, "nothing over quota reaches Kafka"


def test_a_batch_costs_its_event_count(
    ctx: GatewayContext, registry: KeyRegistry, auth: dict[str, str], fake: Any
) -> None:
    """One request with 10 events against a 5 EPS quota is 10 events' worth, not one."""
    from fastapi.testclient import TestClient
    from ingest_gateway.app import create_app

    registry._handle(f"apikey:{KEY_ID}", apikey_message(quota_eps=5))
    client = TestClient(create_app(ctx))
    body = "".join('{"event":"x"}' for _ in range(3))
    assert client.post("/services/collector/event", content=body, headers=auth).status_code == 200
    # 3 of 5 tokens are gone, so the next 3-event request does not fit and is refused whole —
    # a request is never partially accepted.
    assert client.post("/services/collector/event", content=body, headers=auth).status_code == 429
    assert len(fake.messages) == 3
    one = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert one.status_code == 200, "a single event still fits in what is left"
    assert len(fake.messages) == 4, f"and the source is still {SOURCE_ID}"
