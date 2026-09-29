r"""B2: Kafka ``raw.*`` -> encrypted vault segments, against the real broker.

Marked ``int``: ``make test`` skips these, ``make test-int`` runs them.

What these prove end to end, which unit tests cannot:

* every envelope produced to ``raw.*`` ends up in exactly one segment;
* the raw bytes come back byte-identical after zstd + AES-GCM (P1);
* the hash chain recomputes from the segment files alone;
* offsets are committed only after a segment is sealed, so a restart re-reads
  un-archived events instead of losing them;
* a restarted archiver continues the chain from the newest sealed segment.

The vault goes to a temp directory, never ``data/vault``, so a run cannot disturb the
demo stack's own evidence. Each test uses its own consumer group and its own throwaway
``raw.<vendor>`` topic, and subscribes the archiver to that topic alone — the production
``^raw\..*`` pattern would otherwise archive whatever other suites left on the broker.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from pathlib import Path

import pytest
from archiver.archiver import Archiver
from archiver.settings import ArchiverSettings
from confluent_kafka import TopicPartition
from confluent_kafka.admin import AdminClient, NewTopic

from veyra_common.envelope import stamp
from veyra_common.kafka import make_producer
from veyra_common.models import Envelope
from veyra_evidence.chain import walk
from veyra_evidence.keys import LocalKeyProvider
from veyra_evidence.segment import SegmentReader, find_segments

pytestmark = pytest.mark.int

BOOTSTRAP = os.environ.get("VEYRA_KAFKA_BOOTSTRAP", "localhost:29092")
EVENTS = 30

# The demo's messy tier-3 event, a multiline one, non-UTF-8 bytes, and Hindi text: if any
# of these survives the vault intact, the format is not quietly normalising anything.
PAYLOADS = [
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
    "<134>Sep 26 14:05:12 fw01 app[233]: उपयोगकर्ता लॉगिन विफल".encode(),
    b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny",
    b"<134>binary tail: " + bytes(range(256)),
]


@pytest.fixture
def raw_topic() -> str:
    """A throwaway ``raw.<vendor>`` topic, so the archiver's pattern picks it up."""
    name = f"raw.it{uuid.uuid4().hex[:8]}"
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    for future in admin.create_topics(
        [NewTopic(name, num_partitions=1, replication_factor=1)]
    ).values():
        future.result(timeout=60)
    yield name
    for future in admin.delete_topics([name]).values():
        # Cleanup must never fail a passing test.
        with contextlib.suppress(Exception):
            future.result(timeout=60)


@pytest.fixture
def cfg(tmp_path: Path) -> ArchiverSettings:
    return ArchiverSettings(
        _env_file=None,
        kafka_bootstrap=BOOTSTRAP,
        data_dir=tmp_path,
        archive_group=f"archiver-it-{uuid.uuid4().hex[:8]}",
        # Seal on record count in these tests; bytes/seconds stay out of the way.
        segment_max_records=10,
        segment_max_bytes=64 * 1024 * 1024,
        segment_max_seconds=3600,
        metrics_port=0,
    )


@pytest.fixture
def keys(cfg: ArchiverSettings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


def publish(topic: str, count: int = EVENTS) -> list[Envelope]:
    """Produce ``count`` real IF-ENVELOPE records and return them in offset order."""
    producer = make_producer(cfg=_producer_cfg())
    envelopes = []
    for i in range(count):
        env = stamp(
            PAYLOADS[i % len(PAYLOADS)] + f" seq={i}".encode(),
            collector_id="it-archiver",
            transport="syslog_tcp",
            framing_method="newline",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
            peer_ip="172.20.0.21",
        )
        producer.produce(topic, key=env.kafka_key(), value=env.model_dump_json().encode())
        envelopes.append(env)
    assert producer.flush(60) == 0, "not every envelope was delivered"
    return envelopes


def _producer_cfg() -> ArchiverSettings:
    return ArchiverSettings(_env_file=None, kafka_bootstrap=BOOTSTRAP)


def drain(archiver: Archiver, expected: int, *, timeout_s: float = 120.0) -> None:
    """Poll until ``expected`` records are archived, then seal whatever is open."""
    deadline = time.monotonic() + timeout_s
    while archiver.records < expected and time.monotonic() < deadline:
        archiver.poll_once()
    archiver.seal_all()


def all_records(cfg: ArchiverSettings, topic: str, keys: LocalKeyProvider) -> list[dict]:
    """Every archived envelope for a topic, read back out of the segment files."""
    out: list[dict] = []
    for path in find_segments(topic, 0, cfg.vault_dir):
        reader = SegmentReader(path, keys, cfg=cfg)
        # Verifying the chain here means every assertion below is on verified data.
        reader.verify_chain()
        for record in reader.records():
            out.append({"offset": record.offset, "bytes": record.envelope_bytes})
    return out


def test_every_event_is_archived_exactly_once(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """B2 AC2, in miniature: one segment set holds each event exactly once."""
    sent = publish(raw_topic)
    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(archiver, len(sent))
    finally:
        archiver.consumer.close()

    archived = all_records(cfg, raw_topic, keys)
    assert len(archived) == len(sent)
    uids = [json.loads(r["bytes"])["event_uid"] for r in archived]
    assert uids == [e.event_uid for e in sent], "order and identity must both hold"
    assert len(set(uids)) == len(sent), "no event may be archived twice"
    # segment_max_records=10 over 30 events => 3 sealed segments.
    assert len(find_segments(raw_topic, 0, cfg.vault_dir)) == len(sent) // 10


def test_raw_bytes_survive_the_vault(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """P1: what the edge stamped is what comes back out, hash and all."""
    sent = publish(raw_topic, count=10)
    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(archiver, len(sent))
    finally:
        archiver.consumer.close()

    archived = all_records(cfg, raw_topic, keys)
    for original, record in zip(sent, archived, strict=True):
        assert record["bytes"] == original.model_dump_json().encode()
        restored = Envelope.model_validate_json(record["bytes"])
        assert restored.raw_bytes == original.raw_bytes
        assert restored.hash_matches(), "the archived envelope must still verify"
    # The payload that is not valid UTF-8 at all must also be exact.
    binary = [
        Envelope.model_validate_json(r["bytes"])
        for r in archived
        if b"binary tail" in Envelope.model_validate_json(r["bytes"]).raw_bytes
    ]
    assert binary, "the non-UTF-8 payload should be among the archived events"
    assert all(e.raw_bytes.split(b" seq=")[0].endswith(bytes(range(256))) for e in binary)


def test_the_chain_recomputes_across_segments(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """A verifier with only the vault files rebuilds one continuous chain."""
    sent = publish(raw_topic)
    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(archiver, len(sent))
    finally:
        archiver.consumer.close()

    paths = find_segments(raw_topic, 0, cfg.vault_dir)
    assert len(paths) > 1, "this test needs more than one segment"
    prev_last = None
    steps: list[tuple[str, str, int]] = []
    for path in paths:
        reader = SegmentReader(path, keys, cfg=cfg)
        header = reader.header()
        if prev_last is not None:
            assert header["prev_chain_hash_hex"] == prev_last, "segments must link"
        assert reader.verify_chain() == header["last_chain_hash_hex"]
        prev_last = header["last_chain_hash_hex"]
        for record in reader.records():
            envelope = json.loads(record.envelope_bytes)
            steps.append((envelope["raw_sha256"], envelope["event_uid"], record.offset))
    # One walk over every record, independent of the segment boundaries.
    assert prev_last == walk(raw_topic, 0, steps).hex()


def test_sealed_segments_are_read_only(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    sent = publish(raw_topic, count=10)
    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(archiver, len(sent))
    finally:
        archiver.consumer.close()
    for path in find_segments(raw_topic, 0, cfg.vault_dir):
        assert SegmentReader(path, keys, cfg=cfg).is_read_only()
        with pytest.raises(PermissionError), open(path, "ab") as fh:
            fh.write(b"x")


def test_offsets_are_committed_only_after_a_seal(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """Un-sealed events must still be pending in Kafka, so a crash cannot lose them."""
    sent = publish(raw_topic, count=15)  # 10 seal, 5 stay open
    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        deadline = time.monotonic() + 120
        while archiver.records < len(sent) and time.monotonic() < deadline:
            archiver.poll_once()
        assert archiver.segments == 1, "one segment should have sealed at 10 records"
        committed = archiver.consumer.committed([TopicPartition(raw_topic, 0)], timeout=60)[0]
        # Only the sealed segment's offsets are committed; the open five are not.
        assert committed.offset == 10
        archiver.seal_all()
        committed = archiver.consumer.committed([TopicPartition(raw_topic, 0)], timeout=60)[0]
        assert committed.offset == len(sent)
    finally:
        archiver.consumer.close()


def test_a_restarted_archiver_continues_the_chain_and_loses_nothing(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """Stop after some events, restart, and the vault is still one complete chain."""
    first_batch = publish(raw_topic, count=10)
    first = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(first, len(first_batch))
    finally:
        first.consumer.close()
    sealed_first = find_segments(raw_topic, 0, cfg.vault_dir)
    assert len(sealed_first) == 1

    second_batch = publish(raw_topic, count=10)
    restarted = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(restarted, len(second_batch))
    finally:
        restarted.consumer.close()

    paths = find_segments(raw_topic, 0, cfg.vault_dir)
    assert len(paths) == 2, "the restart should have added a segment"
    head_of_first = SegmentReader(paths[0], keys, cfg=cfg).header()["last_chain_hash_hex"]
    second_header = SegmentReader(paths[1], keys, cfg=cfg).header()
    assert second_header["prev_chain_hash_hex"] == head_of_first, "the chain must resume"

    archived = all_records(cfg, raw_topic, keys)
    expected = [e.event_uid for e in first_batch + second_batch]
    assert [json.loads(r["bytes"])["event_uid"] for r in archived] == expected
    assert [r["offset"] for r in archived] == list(range(len(expected)))


def test_a_malformed_record_is_skipped_not_fatal(
    cfg: ArchiverSettings, raw_topic: str, keys: LocalKeyProvider
) -> None:
    """One bad record must not stall the vault."""
    producer = make_producer(cfg=_producer_cfg())
    producer.produce(raw_topic, key="junk", value=b"{not json at all")
    assert producer.flush(60) == 0
    sent = publish(raw_topic, count=10)

    archiver = Archiver.create(cfg, keys, [raw_topic])
    try:
        drain(archiver, len(sent))
    finally:
        archiver.consumer.close()

    archived = all_records(cfg, raw_topic, keys)
    assert [json.loads(r["bytes"])["event_uid"] for r in archived] == [e.event_uid for e in sent]
