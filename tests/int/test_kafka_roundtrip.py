"""Integration tests for the Kafka helpers, against the real broker (`make up`).

Marked ``int``: ``make test`` skips them, ``make test-int`` runs them. They cover the
paths unit tests cannot honestly cover — transactions, read_committed isolation, offsets
committed with the output, and reading a compacted topic from the beginning.

Every later phase depends on these semantics: the normalizer (A3) and archiver (B2) use
``TxnProcessor``, and the normalizer, gateway and router all rebuild state from ``control``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid

import pytest
from confluent_kafka.admin import AdminClient, NewTopic

from veyra_common.envelope import stamp
from veyra_common.kafka import (
    OutputRecord,
    TxnProcessor,
    make_consumer,
    make_producer,
    read_compacted,
)
from veyra_common.models import Envelope
from veyra_common.settings import Settings

pytestmark = pytest.mark.int

# From the host, the broker is reachable on the EXTERNAL listener.
BOOTSTRAP = os.environ.get("VEYRA_KAFKA_BOOTSTRAP_HOST", "localhost:29092")


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap=BOOTSTRAP)


@pytest.fixture
def temp_topics(cfg: Settings) -> list[str]:
    """Two throwaway topics, cleaned up afterwards, so tests never fight the real ones."""
    suffix = uuid.uuid4().hex[:8]
    names = [f"it.in.{suffix}", f"it.out.{suffix}"]
    admin = AdminClient({"bootstrap.servers": cfg.kafka_bootstrap})
    for future in admin.create_topics(
        [NewTopic(n, num_partitions=1, replication_factor=1) for n in names]
    ).values():
        future.result(timeout=30)
    yield names
    for future in admin.delete_topics(names).values():
        # Cleanup must never fail a passing test.
        with contextlib.suppress(Exception):
            future.result(timeout=30)


def _envelope(i: int) -> Envelope:
    return stamp(
        f"<86>Sep 26 14:05:{i:02d} core-lnx-07 sshd[{i}]: Failed password for root "
        f"from 45.12.3.9 port {50000 + i} ssh2".encode(),
        collector_id="it",
        transport="syslog_udp",
        framing_method="datagram",
        source_id="src_lnx_core_07",
        tenant_id="t_ntro_core",
        vendor="linux",
        zone="core",
    )


def test_producer_consumer_roundtrip_preserves_evidence(
    cfg: Settings, temp_topics: list[str]
) -> None:
    topic = temp_topics[0]
    producer = make_producer(cfg=cfg)
    sent = [_envelope(i) for i in range(20)]
    for env in sent:
        producer.produce(topic, key=env.kafka_key(), value=env.model_dump_json().encode())
    assert producer.flush(20) == 0

    consumer = make_consumer(f"it-{uuid.uuid4().hex[:6]}", [topic], cfg=cfg)
    received: list[Envelope] = []
    deadline = time.monotonic() + 30
    while len(received) < len(sent) and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        received.append(Envelope.model_validate_json(msg.value()))
    consumer.close()

    assert len(received) == len(sent)
    assert all(env.hash_matches() for env in received), "raw bytes must survive the round trip"
    assert {e.event_uid for e in received} == {e.event_uid for e in sent}


def test_txn_processor_commits_outputs_and_offsets_together(
    cfg: Settings, temp_topics: list[str]
) -> None:
    src, dst = temp_topics
    producer = make_producer(cfg=cfg)
    for i in range(10):
        producer.produce(src, key="k", value=_envelope(i).model_dump_json().encode())
    producer.flush(20)

    group = f"it-txn-{uuid.uuid4().hex[:6]}"

    def transform(batch: list[object]) -> list[OutputRecord]:
        out = []
        for msg in batch:
            env = Envelope.model_validate_json(msg.value())  # type: ignore[attr-defined]
            out.append(
                OutputRecord(
                    topic=dst,
                    key=env.event_uid,
                    value=json.dumps({"uid": env.event_uid}).encode(),
                )
            )
        return out

    processor = TxnProcessor(
        name="it-txn",
        group=group,
        topics=[src],
        fn=transform,
        instance=uuid.uuid4().hex[:6],
        cfg=cfg,
    )
    deadline = time.monotonic() + 60
    while processor.records < 10 and time.monotonic() < deadline:
        batch = processor.poll_batch()
        if batch:
            processor.process_batch(batch)
    processor.close()
    assert processor.records == 10

    # read_committed must see exactly the 10 outputs, no aborted leftovers.
    consumer = make_consumer(f"it-out-{uuid.uuid4().hex[:6]}", [dst], cfg=cfg)
    seen = 0
    deadline = time.monotonic() + 30
    while seen < 10 and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        seen += 1
    consumer.close()
    assert seen == 10

    # A fresh consumer in the same group must find nothing left: the offsets were
    # committed inside the transaction (this is what makes a restart lossless).
    again = make_consumer(group, [src], cfg=cfg)
    leftovers = 0
    stop = time.monotonic() + 8
    while time.monotonic() < stop:
        msg = again.poll(0.5)
        if msg is not None and not msg.error():
            leftovers += 1
    again.close()
    assert leftovers == 0, "offsets were not committed with the transaction"


def test_aborted_transaction_is_invisible_to_read_committed(
    cfg: Settings, temp_topics: list[str]
) -> None:
    """A crash mid-batch must leave no partial output behind (P2's other half)."""
    _, dst = temp_topics
    producer = make_producer(f"it-abort-{uuid.uuid4().hex[:6]}", cfg=cfg)
    producer.init_transactions()
    producer.begin_transaction()
    producer.produce(dst, key="ghost", value=b'{"ghost":true}')
    producer.flush(10)
    producer.abort_transaction()

    consumer = make_consumer(f"it-abort-{uuid.uuid4().hex[:6]}", [dst], cfg=cfg)
    ghosts = 0
    stop = time.monotonic() + 8
    while time.monotonic() < stop:
        msg = consumer.poll(0.5)
        if msg is not None and not msg.error() and b"ghost" in msg.value():
            ghosts += 1
    consumer.close()
    assert ghosts == 0


def test_read_compacted_rebuilds_state_from_the_beginning(cfg: Settings) -> None:
    """How every control-topic follower starts up (IF-CONTROL)."""
    from veyra_common.topics import TOPIC_CONTROL, create_topics

    create_topics(cfg)
    key = f"source:it_{uuid.uuid4().hex[:6]}"
    producer = make_producer(cfg=cfg)
    producer.produce(TOPIC_CONTROL, key=key, value=json.dumps({"source_id": key}).encode())
    producer.flush(20)

    state = read_compacted(TOPIC_CONTROL, group=f"it-ctrl-{uuid.uuid4().hex[:6]}", cfg=cfg)
    assert key in state
    assert json.loads(state[key])["source_id"] == key  # type: ignore[arg-type]

    # A tombstone removes the value, which is how a revoked key disappears.
    producer.produce(TOPIC_CONTROL, key=key, value=None)
    producer.flush(20)
    state = read_compacted(TOPIC_CONTROL, group=f"it-ctrl-{uuid.uuid4().hex[:6]}", cfg=cfg)
    assert state.get(key) is None


def test_topics_exist_with_profile_partitions(cfg: Settings) -> None:
    """AC2, as a test: every IF-TOPICS topic exists with the profile's partition count."""
    from veyra_common.topics import topic_specs

    admin = AdminClient({"bootstrap.servers": cfg.kafka_bootstrap})
    cluster = admin.list_topics(timeout=20)
    missing = []
    wrong = []
    for spec in topic_specs(cfg):
        meta = cluster.topics.get(spec.name)
        if meta is None:
            missing.append(spec.name)
        elif len(meta.partitions) != spec.partitions:
            wrong.append(f"{spec.name}: {len(meta.partitions)} != {spec.partitions}")
    assert not missing, f"missing topics (run `make topics`): {missing}"
    assert not wrong, f"wrong partition counts: {wrong}"
