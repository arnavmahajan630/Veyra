"""Shared fixtures: a gateway wired to fake Kafka, with keys injected straight into the registry.

The registry is fed through its own `_handle`, the same path a `control` message takes, rather
than by reaching into its dicts — so these tests exercise the message shapes control-api really
publishes (`packages/veyra_common/fixtures/control_apikey.json` is the reference).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gateway_helpers import (
    KEY_ID,
    PEPPER,
    SECRET,
    SOURCE_ID,
    apikey_message,
    source_message,
)
from ingest_gateway.app import GatewayContext, create_app
from ingest_gateway.producer import RawProducer
from ingest_gateway.quota import QuotaLimiter
from ingest_gateway.registry import KeyRegistry
from ingest_gateway.settings import GatewaySettings


class FakeProducer:
    """Records what was produced, and can be told to fail like a real broker would."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, bytes]] = []
        self.flushed = 0
        self.fail_delivery: str | None = None
        self.leave_unflushed = 0
        self.raise_buffer_error = False

    def produce(
        self,
        topic: str,
        value: bytes | None = None,
        key: Any = None,
        on_delivery: Any = None,
        **_: Any,
    ) -> None:
        if self.raise_buffer_error:
            raise BufferError("Local: Queue full")
        self.messages.append((topic, key, value or b""))
        if on_delivery is not None:
            on_delivery(self.fail_delivery, None)

    def flush(self, timeout: float = 10) -> int:
        self.flushed += 1
        return self.leave_unflushed

    # ---- helpers for assertions
    def envelopes(self) -> list[dict[str, Any]]:
        return [json.loads(value) for _, _, value in self.messages]

    def topics(self) -> list[str]:
        return [topic for topic, _, _ in self.messages]


@pytest.fixture
def cfg(tmp_path: Path) -> GatewaySettings:
    return GatewaySettings(_env_file=None, data_dir=tmp_path)


@pytest.fixture
def registry(cfg: GatewaySettings) -> KeyRegistry:
    reg = KeyRegistry(PEPPER, cfg=cfg)
    reg._handle(f"apikey:{KEY_ID}", apikey_message())
    reg._handle(f"source:{SOURCE_ID}", source_message())
    reg.reader.ready.set()  # the control backlog is read; no broker needed for unit tests
    return reg


@pytest.fixture
def producer(cfg: GatewaySettings) -> RawProducer:
    return RawProducer(cfg, producer=FakeProducer())


@pytest.fixture
def ctx(cfg: GatewaySettings, registry: KeyRegistry, producer: RawProducer) -> GatewayContext:
    return GatewayContext(
        cfg=cfg,
        registry=registry,
        producer=producer,
        limiter=QuotaLimiter(),
        collector_id="gw-test",
    )


@pytest.fixture
def client(ctx: GatewayContext) -> TestClient:
    return TestClient(create_app(ctx))


@pytest.fixture
def fake(producer: RawProducer) -> FakeProducer:
    return producer.producer  # type: ignore[return-value]


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Splunk {SECRET}"}
