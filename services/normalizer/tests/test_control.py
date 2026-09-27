"""The control follower's readiness rule, which is easy to get wrong and expensive when it is.

A restarted normalizer that declares itself ready before it has read `control` normalizes every
event as tier 4 (no contract), and nothing downstream looks broken — the events flow, they are
just useless. That is the first entry in S1's "common failure modes" table, and it happened here
for real during A3, so the rule is pinned by tests with a fake consumer.
"""

from __future__ import annotations

import json
import time
from typing import Any

from normalizer.control import ControlFollower

from veyra_common.settings import Settings
from veyra_engine import Engine, EngineContext


class FakeMessage:
    """Minimal stand-in for confluent_kafka.Message."""

    def __init__(self, key: str, value: dict[str, Any] | None) -> None:
        self._key = key.encode()
        self._value = None if value is None else json.dumps(value).encode()

    def key(self) -> bytes:
        return self._key

    def value(self) -> bytes | None:
        return self._value

    def error(self) -> None:
        return None


class FakePartition:
    def __init__(self, partition: int, offset: int = -1001) -> None:
        self.partition = partition
        self.offset = offset


class FakeConsumer:
    """A consumer that is unassigned for the first few polls, then serves a backlog.

    This is the shape that broke the original implementation: polls return None while the
    group is still joining, which the old code read as "the topic is empty".
    """

    def __init__(self, messages: list[FakeMessage], *, unassigned_polls: int = 4) -> None:
        self.messages = list(messages)
        self.unassigned_polls = unassigned_polls
        self.polls = 0
        self.delivered = 0
        self.closed = False

    def poll(self, timeout: float) -> FakeMessage | None:
        self.polls += 1
        if self.polls <= self.unassigned_polls:
            return None
        if self.messages:
            self.delivered += 1
            return self.messages.pop(0)
        return None

    def assignment(self) -> list[FakePartition]:
        if self.polls <= self.unassigned_polls:
            return []
        return [FakePartition(0)]

    def get_watermark_offsets(
        self, partition: Any, timeout: float, cached: bool
    ) -> tuple[int, int]:
        return 0, self.delivered + len(self.messages)

    def position(self, partitions: list[Any]) -> list[FakePartition]:
        return [FakePartition(0, offset=self.delivered)]

    def close(self) -> None:
        self.closed = True


def contract_message(contract_id: str, source_id: str) -> FakeMessage:
    return FakeMessage(
        f"contract:{contract_id}",
        {
            "id": contract_id,
            "version": 1,
            "state": "active",
            "tenant_id": "t_x",
            "sources": [source_id],
            "compiled": {
                "contract": contract_id,
                "version": 1,
                "sources": [source_id],
                "envelope": [],
                "templates": [],
            },
            "candidate": None,
            "published_at": "2026-09-26T00:00:00.000000000Z",
        },
    )


def follower_with(messages: list[FakeMessage], monkeypatch: Any, **kwargs: Any) -> ControlFollower:
    engine = Engine(EngineContext())
    fake = FakeConsumer(messages, **kwargs)
    monkeypatch.setattr("normalizer.control.make_consumer", lambda *a, **k: fake)
    follower = ControlFollower(engine, cfg=Settings(_env_file=None), group="test", idle_ms=200)
    follower._fake = fake  # type: ignore[attr-defined]
    return follower


def test_not_ready_until_the_whole_backlog_is_read(monkeypatch: Any) -> None:
    """The regression test for the real bug: polls returning None while unassigned."""
    messages = [contract_message("c1", "src_1"), contract_message("c2", "src_2")]
    follower = follower_with(messages, monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10), "follower never became ready"
        # Ready must mean "state is complete", not merely "no messages arrived recently".
        assert follower.engine.contracts_loaded == 2
        assert follower.engine.contract_for_source("src_1") is not None
        assert follower.engine.contract_for_source("src_2") is not None
    finally:
        follower.stop()


def test_ready_on_a_genuinely_empty_topic(monkeypatch: Any) -> None:
    """An empty control topic must not hang the service forever — just report zero contracts."""
    follower = follower_with([], monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10)
        assert follower.engine.contracts_loaded == 0
    finally:
        follower.stop()


def test_a_tombstone_retires_a_contract(monkeypatch: Any) -> None:
    messages = [
        contract_message("c1", "src_1"),
        FakeMessage("contract:c1", None),  # tombstone
    ]
    follower = follower_with(messages, monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10)
        assert follower.engine.contracts_loaded == 0, "a tombstoned contract must be gone"
    finally:
        follower.stop()


def test_source_records_extend_a_contract_source_list(monkeypatch: Any) -> None:
    """A source may point at a contract that does not list it yet (both sides live in control)."""
    messages = [
        contract_message("authsrv", "src_old"),
        FakeMessage(
            "source:src_new",
            {
                "source_id": "src_new",
                "tenant_id": "t_x",
                "vendor": "custom",
                "zone": "dmz",
                "transport": "http_hec_event",
                "contract_id": "authsrv",
                "expected_eps": 1.0,
                "salt_buckets": 1,
                "status": "active",
            },
        ),
    ]
    follower = follower_with(messages, monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10)
        assert follower.engine.contract_for_source("src_new") is not None
        assert follower.engine.contract_for_source("src_old") is not None
    finally:
        follower.stop()


def test_vocab_and_enrich_reach_the_engine_context(monkeypatch: Any) -> None:
    messages = [
        FakeMessage(
            "vocab:status_words",
            {"name": "status_words", "version": 1, "entries": {"FAILED": {"status_id": 2}}},
        ),
        FakeMessage(
            "enrich:zone_map",
            {"name": "zone_map", "version": 1, "rows": [{"match_value": "1.2.3.4", "zone": "dmz"}]},
        ),
    ]
    follower = follower_with(messages, monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10)
        assert follower.engine.ctx.vocab["status_words"]["entries"]["FAILED"]["status_id"] == 2
        assert follower.engine.ctx.enrich["zone_map"][0]["zone"] == "dmz"
    finally:
        follower.stop()


def test_malformed_control_json_is_logged_and_skipped(monkeypatch: Any) -> None:
    class Broken(FakeMessage):
        def __init__(self) -> None:
            super().__init__("contract:bad", {})
            self._value = b"{not json"

    messages = [Broken(), contract_message("c1", "src_1")]
    follower = follower_with(messages, monkeypatch)
    follower.start()
    try:
        assert follower.wait_ready(timeout=10)
        assert follower.engine.contracts_loaded == 1, "the good contract must still load"
    finally:
        follower.stop()


def test_stop_closes_the_consumer(monkeypatch: Any) -> None:
    follower = follower_with([contract_message("c1", "src_1")], monkeypatch)
    follower.start()
    assert follower.wait_ready(timeout=10)
    follower.stop()
    time.sleep(0.2)
    assert follower._fake.closed  # type: ignore[attr-defined]
