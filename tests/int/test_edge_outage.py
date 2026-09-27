"""A1 AC3: an edge must survive a Kafka outage without losing or duplicating events.

Separate file and marked ``slow`` because it stops and starts the broker, which takes a
couple of minutes and disturbs everything else on the stack:

    make test-int-slow      # or: pytest -m "int and slow" tests/int/test_edge_outage.py

The guarantee under test is the one v1 §13 makes for an edge failure: the sink's disk buffer
with ``when_full = "block"`` holds events while the broker is unreachable and applies
backpressure instead of dropping them (P2).
"""

from __future__ import annotations

import socket
import subprocess
import time
import uuid

import pytest
from confluent_kafka import TopicPartition

from veyra_common.kafka import make_consumer
from veyra_common.models import Envelope
from veyra_common.settings import Settings

pytestmark = [pytest.mark.int, pytest.mark.slow]

CORE_UDP = 5524
EVENTS = 50
OUTAGE_SECONDS = 60


def compose(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.runtime",
            "-f",
            "compose/docker-compose.yml",
            "-f",
            "compose/docker-compose.wazuh.yml",
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )


@pytest.fixture
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


def end_positions(cfg: Settings) -> list[TopicPartition]:
    """Where every ``raw.*`` partition ends right now (captured before the outage)."""
    consumer = make_consumer(f"it-outage-probe-{uuid.uuid4().hex[:6]}", cfg=cfg)
    try:
        metadata = consumer.list_topics(timeout=20)
        positions = []
        for topic, meta in metadata.topics.items():
            if not topic.startswith("raw."):
                continue
            for partition in meta.partitions:
                _, high = consumer.get_watermark_offsets(
                    TopicPartition(topic, partition), timeout=10, cached=False
                )
                positions.append(TopicPartition(topic, partition, high))
        return positions
    finally:
        consumer.close()


def test_ac1_full_size_bulk_udp_and_tcp(cfg: Settings) -> None:
    """AC1 at its stated size: 1000 UDP + 1000 TCP at ~200 EPS, every envelope valid.

    Slow-marked because it takes about 20 s of steady sending; `make test-int` runs a
    corpus-sized version of the same assertions on every pass.
    """
    marker = f"bulk{uuid.uuid4().hex[:8]}".encode()
    total = 1000
    positions = end_positions(cfg)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    started = time.monotonic()
    try:
        for i in range(total):
            payload = f"<86>Sep 26 14:05:12 core-lnx-07 sshd[{i}]: bulk udp {i} ".encode() + marker
            sock.sendto(payload, ("127.0.0.1", CORE_UDP))
            due = started + (i + 1) / 200.0  # ~200 EPS, absolute schedule
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    finally:
        sock.close()

    tcp = socket.create_connection(("127.0.0.1", 5525), timeout=20)
    try:
        for i in range(total):
            tcp.sendall(
                f"<86>Sep 26 14:05:12 core-lnx-07 sshd[{i}]: bulk tcp {i} ".encode()
                + marker
                + b"\n"
            )
    finally:
        tcp.close()

    consumer = make_consumer(f"it-bulk-{uuid.uuid4().hex[:6]}", cfg=cfg)
    consumer.assign(positions)
    valid = 0
    invalid = 0
    uids: set[str] = set()
    deadline = time.monotonic() + 180
    while valid + invalid < total * 2 and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        try:
            envelope = Envelope.model_validate_json(msg.value())
        except Exception:  # a bad record is counted, never raised
            invalid += 1
            continue
        if marker not in envelope.raw_bytes:
            continue
        if not envelope.hash_matches():
            invalid += 1
            continue
        valid += 1
        uids.add(envelope.event_uid)
    consumer.close()

    assert invalid == 0, f"{invalid} invalid envelopes"
    # The last TCP event waits for the reducer to expire, so allow a small tail shortfall
    # rather than pretending UDP is lossless; anything more than a handful is a real defect.
    assert valid >= total * 2 - 5, f"only {valid} of {total * 2} arrived"
    assert len(uids) == valid, "duplicate event_uid in the bulk run"


def test_ac3_kafka_outage_loses_nothing_and_duplicates_nothing(cfg: Settings) -> None:
    marker = f"outage{uuid.uuid4().hex[:8]}".encode()
    positions = end_positions(cfg)

    assert compose("stop", "kafka").returncode == 0, "could not stop kafka"
    try:
        # Send while the broker is down. The edge should buffer these to disk.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for i in range(EVENTS):
                payload = (
                    f"<86>Sep 26 14:05:12 core-lnx-07 sshd[{i}]: outage probe {i} ".encode()
                    + marker
                )
                sock.sendto(payload, ("127.0.0.1", CORE_UDP))
                time.sleep(0.02)
        finally:
            sock.close()
        time.sleep(OUTAGE_SECONDS)
    finally:
        assert compose("start", "kafka").returncode == 0, "could not start kafka"

    # Wait for the broker to accept connections again.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        probe = make_consumer(f"it-outage-wait-{uuid.uuid4().hex[:6]}", cfg=cfg)
        try:
            if probe.list_topics(timeout=10).topics:
                break
        except Exception:  # the broker is still starting
            pass
        finally:
            probe.close()
        time.sleep(5)

    consumer = make_consumer(f"it-outage-read-{uuid.uuid4().hex[:6]}", cfg=cfg)
    consumer.assign(positions)
    seen: list[str] = []
    deadline = time.monotonic() + 180
    while len(seen) < EVENTS and time.monotonic() < deadline:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        envelope = Envelope.model_validate_json(msg.value())
        if marker in envelope.raw_bytes:
            assert envelope.hash_matches()
            seen.append(envelope.event_uid)
    consumer.close()

    assert len(seen) == EVENTS, f"lost events across the outage: {len(seen)} of {EVENTS} arrived"
    assert len(set(seen)) == EVENTS, "duplicate event_uid after the outage"
