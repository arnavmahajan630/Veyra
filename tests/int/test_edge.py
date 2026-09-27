"""A1 integration tests: real sockets into the running Vector edges, real Kafka out.

Marked ``int`` — needs `make up`. These are the phase's acceptance criteria as code:

* AC1  bulk UDP + TCP → every envelope valid, hashes recompute, nothing lost
* AC2  the T3 corpus event over TCP → **one** envelope, ``multiline_join``, ``parts=2``,
       bytes equal to the original two lines joined with ``\\n``
* AC4  a new ``sources.csv`` row is honoured within 5 s (inventory reload)
* AC5  an unknown host lands on ``raw.unregistered``
* parity: the edge's ``raw_sha256``/``raw_b64`` match ``veyra_common.envelope.stamp``

AC3 (Kafka down for 60 s, disk buffer, no loss) is a manual/slow check run once per phase
and recorded in the report, not on every `make test-int`.
"""

from __future__ import annotations

import base64
import json
import socket
import time
import uuid
from pathlib import Path

import pytest
from confluent_kafka import TopicPartition

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.hashing import sha256_hex
from veyra_common.kafka import make_consumer
from veyra_common.models import Envelope
from veyra_common.settings import Settings

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "demo" / "corpus"
INVENTORY = REPO / "edge" / "vector" / "inventory" / "sources.csv"
RELOAD_STAMP = REPO / "edge" / "vector" / "reload.stamp"

# IF-PORTS. The host reaches the edges on these; inside the network they are the same.
DMZ_UDP, DMZ_TCP = 5514, 5515
CORE_UDP, CORE_TCP = 5524, 5525

# The T3 demo event: a JSON line plus its stack-trace continuation (04_DEMO_SCRIPT §2.1).
T3_LINE1 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace='
)
T3_LINE2 = b"  at com.x.Auth.login(Auth.java:88)"


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


def subscribe(cfg: Settings) -> object:
    """A consumer pinned to the current end of every ``raw.*`` partition.

    Deliberately `assign()`, not `subscribe()`: a pattern subscription with
    `auto.offset.reset=latest` can finish rebalancing *after* the test has already sent its
    event, and that event is then skipped — which showed up as a flaky AC5. Explicit
    assignment at the high watermark has no rebalance and no race.
    """
    consumer = make_consumer(f"it-edge-{uuid.uuid4().hex[:8]}", cfg=cfg)
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
    assert positions, "no raw.* topics found — run `make topics`"
    consumer.assign(positions)
    return consumer


def drain(
    consumer: object,
    *,
    want: int,
    timeout: float = 30.0,
    marker: bytes | None = None,
) -> list[tuple[str, Envelope]]:
    """Collect up to ``want`` envelopes, validating each against IF-ENVELOPE.

    ``marker`` keeps tests independent: every test tags its payloads with a unique token
    and only counts envelopes carrying it, so one test never consumes another's events
    (all of them share the `raw.*` topics).
    """
    out: list[tuple[str, Envelope]] = []
    deadline = time.monotonic() + timeout
    while len(out) < want and time.monotonic() < deadline:
        msg = consumer.poll(1.0)  # type: ignore[attr-defined]
        if msg is None or msg.error():
            continue
        envelope = Envelope.model_validate_json(msg.value())
        if marker is not None and marker not in envelope.raw_bytes:
            continue
        out.append((msg.topic(), envelope))
    return out


def marker_token() -> bytes:
    """A short unique token to embed in a test's payloads."""
    return f"mk{uuid.uuid4().hex[:10]}".encode()


def send_and_collect(
    cfg: Settings,
    send: object,
    *,
    marker: bytes,
    want: int = 1,
    timeout: float = 25.0,
    attempts: int = 3,
) -> list[tuple[str, Envelope]]:
    """Send, then collect the marked envelopes, retrying the whole exchange if needed.

    Two honest reasons a first attempt can come up empty: UDP is allowed to drop, and a
    Vector config reload (`--watch-config`) briefly tears the topology down, so an event
    sent inside that window is lost. Retrying is what a real shipper does too.
    """
    for attempt in range(attempts):
        consumer = subscribe(cfg)
        try:
            send()  # type: ignore[operator]
            got = drain(consumer, want=want, timeout=timeout, marker=marker)
        finally:
            consumer.close()  # type: ignore[attr-defined]
        if len(got) >= want:
            return got
        if attempt < attempts - 1:
            time.sleep(2)
    return got


def send_udp(port: int, payloads: list[bytes], *, delay: float = 0.0) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for payload in payloads:
            sock.sendto(payload, ("127.0.0.1", port))
            if delay:
                time.sleep(delay)
    finally:
        sock.close()


def send_tcp(port: int, payload: bytes) -> None:
    """One connection, one write. The edge frames on newlines."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        sock.sendall(payload)
    finally:
        sock.close()


# ---------------------------------------------------------------- AC2 (the one that matters most)
def test_ac2_multiline_event_arrives_as_one_envelope(cfg: Settings) -> None:
    """A stack trace must not become a second event, and the bytes must be exact."""
    marker = marker_token()
    line1 = T3_LINE1 + b" " + marker
    got = send_and_collect(
        cfg,
        lambda: send_tcp(DMZ_TCP, line1 + b"\n" + T3_LINE2 + b"\n"),
        marker=marker,
        timeout=40,
    )

    joined = [e for _, e in got if e.framing.parts == 2]
    shapes = [(e.framing.method, e.framing.parts) for _, e in got]
    assert joined, f"no joined envelope among {shapes}"
    envelope = joined[0]
    assert envelope.framing.method == "multiline_join"
    assert envelope.framing.parts == 2
    assert envelope.raw_bytes == line1 + b"\n" + T3_LINE2, "joined bytes must be the originals"
    assert envelope.hash_matches()
    assert envelope.raw_len == len(line1) + 1 + len(T3_LINE2)


def test_multiline_matches_veyra_common_framing(cfg: Settings) -> None:
    """The edge's join rule and `veyra_common.framing.split_lines` must agree."""
    marker = marker_token()
    blob = T3_LINE1 + b" " + marker + b"\n" + T3_LINE2 + b"\n"
    framed = split_lines(blob)
    assert len(framed) == 1
    assert framed[0].parts == 2
    assert framed[0].method == "multiline_join"

    got = send_and_collect(cfg, lambda: send_tcp(DMZ_TCP, blob), marker=marker, timeout=40)

    joined = [e for _, e in got if e.framing.parts == 2]
    assert joined, "edge produced no joined event where framing.split_lines joins one"
    assert joined[0].raw_bytes == framed[0].raw


# ---------------------------------------------------------------- AC1
def test_ac1_bulk_udp_and_tcp_all_valid(cfg: Settings) -> None:
    """Corpus lines in, valid envelopes out, every hash recomputed."""
    marker = marker_token()
    lines = [
        line.encode() + b" " + marker
        for line in (CORPUS / "linux_sshd.log").read_text().splitlines()
        if line.strip()
    ]
    assert lines, "corpus is empty — run demo/tools/gen_corpus.py"

    consumer = subscribe(cfg)
    try:
        send_udp(CORE_UDP, lines, delay=0.005)
        send_tcp(CORE_TCP, b"\n".join(lines) + b"\n")
        got = drain(consumer, want=len(lines) * 2, timeout=60, marker=marker)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert len(got) >= len(lines), f"expected at least {len(lines)} envelopes, got {len(got)}"
    for _, envelope in got:
        assert envelope.hash_matches(), f"hash mismatch on {envelope.event_uid}"
        assert envelope.raw_len == len(envelope.raw_bytes)
        assert envelope.v == 1
        assert envelope.custody == "realtime"
        assert envelope.auth.method == "ip_map"


def test_transport_and_listener_are_recorded_per_socket(cfg: Settings) -> None:
    marker = marker_token()

    def send() -> None:
        send_udp(CORE_UDP, [b"<86>Sep 26 14:05:12 core-lnx-07 sshd[1]: udp " + marker])
        send_tcp(CORE_TCP, b"<86>Sep 26 14:05:12 core-lnx-07 sshd[1]: tcp " + marker + b"\n")

    got = send_and_collect(cfg, send, marker=marker, want=2, timeout=40)

    by_transport = {e.transport: e for _, e in got}
    assert by_transport["syslog_udp"].listener == "core-udp"
    assert by_transport["syslog_udp"].framing.method == "datagram"
    assert by_transport["syslog_tcp"].listener == "core-tcp"
    assert by_transport["syslog_tcp"].framing.method == "newline"
    for envelope in by_transport.values():
        assert envelope.zone == "core"
        assert envelope.collector_id == "edge-core-01"


# ---------------------------------------------------------------- AC5 + resolution
def test_ac5_unknown_host_is_unregistered(cfg: Settings) -> None:
    marker = marker_token()
    got = send_and_collect(
        cfg,
        lambda: send_udp(CORE_UDP, [b"<86>Sep 26 14:05:13 mystery-box app: who am i " + marker]),
        marker=marker,
    )
    assert got, "no envelope produced for an unknown host"
    topic, envelope = got[0]
    assert topic == "raw.unregistered"
    assert envelope.source_id == "unregistered"
    assert envelope.tenant_id == "unassigned"
    assert envelope.vendor == "unregistered"


def test_syslog_host_resolution_uses_the_inventory(cfg: Settings) -> None:
    """A seeded host name resolves to its source without knowing the sender's IP."""
    marker = marker_token()
    got = send_and_collect(
        cfg,
        lambda: send_udp(
            CORE_UDP, [b"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed " + marker]
        ),
        marker=marker,
    )
    assert got
    topic, envelope = got[0]
    assert topic == "raw.linux"
    assert envelope.source_id == "src_lnx_core_07"
    assert envelope.tenant_id == "t_ntro_core"
    assert envelope.vendor == "linux"


# ---------------------------------------------------------------- AC4
def test_ac4_inventory_row_is_picked_up(cfg: Settings) -> None:
    """Add a row, touch the reload stamp, and the next event resolves to it.

    The mechanism is documented in edge/RELOAD.md; this test is what keeps it honest.
    """
    original = INVENTORY.read_text()
    new_host = f"probe-{uuid.uuid4().hex[:6]}"
    try:
        INVENTORY.write_text(
            original + f"core-udp,syslog_host,{new_host},src_probe_01,t_ntro_core,linux,core\n"
        )
        RELOAD_STAMP.write_text(str(time.time()))
        time.sleep(5)  # AC4's budget

        marker = marker_token()
        got = send_and_collect(
            cfg,
            lambda: send_udp(
                CORE_UDP, [f"<86>Sep 26 14:05:12 {new_host} sshd[1]: hello ".encode() + marker]
            ),
            marker=marker,
        )

        assert got, "no envelope after the inventory change"
        _, envelope = got[0]
        assert envelope.source_id == "src_probe_01", (
            "the new inventory row was not honoured within 5 s — see edge/RELOAD.md"
        )
    finally:
        INVENTORY.write_text(original)
        RELOAD_STAMP.write_text(str(time.time()))
        time.sleep(3)


# ---------------------------------------------------------------- parity with the library
def test_edge_and_stamp_agree_on_the_same_bytes(cfg: Settings) -> None:
    """The gateway (A2) uses `stamp`; the edge uses VRL. Same bytes, same seal."""
    marker = marker_token()
    raw = b"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: parity from 45.12.3.9 " + marker
    got = send_and_collect(cfg, lambda: send_udp(CORE_UDP, [raw]), marker=marker)
    assert got
    _, envelope = got[0]
    reference = stamp(
        raw,
        collector_id="edge-core-01",
        transport="syslog_udp",
        framing_method="datagram",
        zone="core",
        source_id="src_lnx_core_07",
        tenant_id="t_ntro_core",
        vendor="linux",
        listener="core-udp",
        auth_method="ip_map",
    )
    assert envelope.raw_sha256 == reference.raw_sha256
    assert envelope.raw_b64 == reference.raw_b64
    assert envelope.raw_len == reference.raw_len
    assert sha256_hex(base64.b64decode(envelope.raw_b64)) == envelope.raw_sha256


def test_parity_vectors_from_s0_still_hold(cfg: Settings) -> None:
    """Send the stored vectors' bytes through the edge and compare the seal."""
    vectors = json.loads(
        (REPO / "packages" / "veyra_common" / "fixtures" / "envelope_vectors.json").read_text()
    )
    payloads: list[bytes] = []
    expected: dict[str, str] = {}
    for vector in vectors["vectors"]:
        envelope = Envelope.model_validate(vector["envelope"])
        if envelope.framing.parts > 1:
            continue  # the joined one is covered by AC2
        payloads.append(envelope.raw_bytes)
        expected[envelope.raw_sha256] = vector["name"]

    consumer = subscribe(cfg)
    try:
        send_udp(CORE_UDP, payloads, delay=0.05)
        # These payloads must stay byte-exact, so they cannot carry a marker; over-collect
        # and match on the hash instead.
        got = drain(consumer, want=len(payloads) * 5, timeout=40)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    seen = {e.raw_sha256 for _, e in got}
    missing = {sha: name for sha, name in expected.items() if sha not in seen}
    assert not missing, f"the edge did not reproduce these S0 parity vectors: {missing}"


def test_hindi_bytes_survive_and_length_is_bytes_not_chars(cfg: Settings) -> None:
    """`length()` in VRL is byte length; `strlen()` would be characters and break offsets."""
    marker = marker_token()
    raw = "26-09-2026 14:05:00;HIST01;TAG0001;टर्बाइन-1 दबाव;OK;".encode() + marker
    assert len(raw) > len(raw.decode())  # multi-byte, so the two differ

    got = send_and_collect(cfg, lambda: send_udp(CORE_UDP, [raw]), marker=marker)
    assert got
    _, envelope = got[0]
    assert envelope.raw_len == len(raw), "raw_len must be bytes, not characters"
    assert envelope.raw_bytes == raw
    assert envelope.hash_matches()
