"""The archiver publishes IF-VAULT-INDEX at each seal (B2).

Nothing produced to `vault_index` before this: the topic was created and the lineage indexer
subscribed to it, but no service wrote to it, so the `segments` and `vault_locations` tables
stayed empty, the console's vault panel had nothing to show, and the evidence API could only
find an event by scanning the whole vault.

No Kafka here — the producer is a stub, and the records are validated against the IF-VAULT-INDEX
models, which is what the lineage indexer parses them with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from archiver.archiver import Archiver
from archiver.settings import ArchiverSettings

from veyra_common.models.records import VaultIndexEvent, VaultIndexSegment
from veyra_common.topics import TOPIC_VAULT_INDEX
from veyra_evidence.keys import LocalKeyProvider


class FakeProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes, bytes | None]] = []
        self.flushed = 0

    def produce(self, topic: str, value: bytes, key: bytes | None = None, **_: Any) -> None:
        self.sent.append((topic, value, key))

    def flush(self, _timeout: float | None = None) -> int:
        self.flushed += 1
        return 0


class FakeConsumer:
    """Only what `seal(commit=False)` and `handle` touch."""

    def commit(self, **_: Any) -> None:  # pragma: no cover - commit=False in these tests
        raise AssertionError("these tests seal with commit=False")


class FakeMessage:
    def __init__(self, topic: str, partition: int, offset: int, value: bytes) -> None:
        self._topic, self._partition, self._offset, self._value = topic, partition, offset, value

    def error(self) -> None:
        return None

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset

    def value(self) -> bytes:
        return self._value


def envelope(uid: str) -> bytes:
    return json.dumps(
        {
            "event_uid": uid,
            "raw_sha256": "a" * 64,
            "tenant_id": "t_ntro_core",
            "source_id": "src_lnx_core_07",
        }
    ).encode()


@pytest.fixture
def cfg(tmp_path: Path) -> ArchiverSettings:
    return ArchiverSettings(
        _env_file=None,
        data_dir=tmp_path,
        segment_max_records=1000,
        segment_max_bytes=64 * 1024 * 1024,
        segment_max_seconds=3600,
        metrics_port=0,
    )


@pytest.fixture
def archiver(cfg: ArchiverSettings) -> tuple[Archiver, FakeProducer]:
    producer = FakeProducer()
    return (
        Archiver(cfg, FakeConsumer(), keys=LocalKeyProvider(cfg=cfg), producer=producer),
        producer,
    )


UIDS = [f"00000000-0000-7000-8000-00000000000{n}" for n in range(1, 4)]


def archive_three(archiver: Archiver) -> tuple[str, int]:
    for index, uid in enumerate(UIDS):
        archiver.handle(FakeMessage("raw.linux", 0, 100 + index, envelope(uid)))
    sealed = archiver.seal(("raw.linux", 0), commit=False)
    assert sealed is not None
    return sealed.segment_id, sealed.last_offset


def test_a_seal_publishes_one_segment_record_and_one_per_event(
    archiver: tuple[Archiver, FakeProducer],
) -> None:
    arch, producer = archiver
    segment_id, _ = archive_three(arch)

    assert [topic for topic, _, _ in producer.sent] == [TOPIC_VAULT_INDEX] * 4
    kinds = [json.loads(value)["kind"] for _, value, _ in producer.sent]
    assert kinds == ["segment", "event", "event", "event"]
    # Keyed by segment so every record of one segment lands on one partition, in order.
    assert {key for _, _, key in producer.sent} == {segment_id.encode()}
    assert producer.flushed == 1


def test_the_segment_record_matches_the_sealed_header(
    archiver: tuple[Archiver, FakeProducer],
) -> None:
    arch, producer = archiver
    segment_id, last_offset = archive_three(arch)
    record = VaultIndexSegment.model_validate_json(producer.sent[0][1])

    assert record.segment_id == segment_id
    assert (record.raw_topic, record.partition) == ("raw.linux", 0)
    assert (record.first_offset, record.last_offset) == (100, last_offset)
    assert record.record_count == 3
    sealed = arch.sealed[-1]
    assert record.segment_digest_hex == sealed.digest_hex
    assert record.last_chain_hash_hex == sealed.header["last_chain_hash_hex"]


def test_every_event_record_can_locate_its_own_bytes(
    archiver: tuple[Archiver, FakeProducer],
) -> None:
    """These rows become `vault_locations`, which is how an event is found without a scan."""
    arch, producer = archiver
    segment_id, _ = archive_three(arch)
    events = [VaultIndexEvent.model_validate_json(value) for _, value, _ in producer.sent[1:]]

    assert [e.event_uid for e in events] == UIDS
    assert [e.record_idx for e in events] == [0, 1, 2]
    assert [e.offset for e in events] == [100, 101, 102]
    assert {e.segment_id for e in events} == {segment_id}
    assert all(len(e.chain_hash_hex) == 64 for e in events)


def test_a_broker_that_refuses_the_index_does_not_lose_the_segment(
    cfg: ArchiverSettings,
) -> None:
    """The evidence is already on disk; the index is a lookup shortcut, not the record."""

    class BrokenProducer(FakeProducer):
        def produce(self, topic: str, value: bytes, key: bytes | None = None, **_: Any) -> None:
            raise RuntimeError("broker unavailable")

    arch = Archiver(cfg, FakeConsumer(), keys=LocalKeyProvider(cfg=cfg), producer=BrokenProducer())
    segment_id, _ = archive_three(arch)
    assert arch.segments == 1
    assert Path(arch.sealed[-1].path).exists()
    assert arch.sealed[-1].segment_id == segment_id


def test_a_second_seal_does_not_re_announce_the_first_segments_events(
    archiver: tuple[Archiver, FakeProducer],
) -> None:
    arch, producer = archiver
    archive_three(arch)
    arch.handle(FakeMessage("raw.linux", 0, 200, envelope(UIDS[0])))
    arch.seal(("raw.linux", 0), commit=False)

    second = [json.loads(value) for _, value, _ in producer.sent[4:]]
    assert [r["kind"] for r in second] == ["segment", "event"]
    assert [r["offset"] for r in second if r["kind"] == "event"] == [200]
