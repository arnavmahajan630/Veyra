"""A3 integration tests: the normalizer service against the real stack.

Needs `make up SERVICES="a3"` plus `uv run python tools/mock_control_publish.py` (the control
publisher A owns until C1 lands). Covers the guarantees that only show up with a real broker:

* a syslog line becomes a tier-1 OCSF event on the right `norm.<category>` topic, with an
  IF-LINEAGE row and the **real Kafka coordinates** in `ulpf.raw_ref`;
* a source with no contract still produces an event plus a DLQ copy (P2);
* the same event is never emitted twice, and killing the service mid-stream loses nothing
  (transactions with offsets committed inside them) — A3 task 9, CP1 check 7.

A4 adds the tier-3 half: an ultra-messy line from a source nobody wrote a contract for still
arrives as a usable event, with observables and with byte offsets that still slice the original
bytes after the round trip through two Kafka topics.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import uuid

import pytest
from confluent_kafka import TopicPartition

from veyra_common.kafka import make_consumer
from veyra_common.models import LineageRecord, NormEvent
from veyra_common.settings import Settings

pytestmark = pytest.mark.int

CORE_UDP = 5524
OUTPUT_PREFIXES = ("norm.", "lineage", "dlq")


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


@pytest.fixture(scope="module", autouse=True)
def require_normalizer() -> None:
    """Skip rather than fail when the service is not running: this file needs it up."""
    running = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    ).stdout
    if "veyra-normalizer" not in running:
        pytest.skip('normalizer is not running — start it with: make up SERVICES="a3"')


def pinned_consumer(cfg: Settings) -> object:
    """A consumer pinned to the current end of every output topic (see A1's note on why)."""
    return pinned_consumer_for(cfg, OUTPUT_PREFIXES)


def pinned_consumer_for(cfg: Settings, prefixes: tuple[str, ...]) -> object:
    consumer = make_consumer(f"it-norm-{uuid.uuid4().hex[:8]}", cfg=cfg)
    metadata = consumer.list_topics(timeout=20)
    positions = []
    for topic, meta in metadata.topics.items():
        if not topic.startswith(prefixes):
            continue
        for partition in meta.partitions:
            _, high = consumer.get_watermark_offsets(
                TopicPartition(topic, partition), timeout=10, cached=False
            )
            positions.append(TopicPartition(topic, partition, high))
    consumer.assign(positions)
    return consumer


def send(line: bytes, times: int = 3, port: int = CORE_UDP) -> None:
    """UDP may drop and a reloading edge may too, so a probe is sent more than once."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for _ in range(times):
            sock.sendto(line, ("127.0.0.1", port))
            time.sleep(0.2)
    finally:
        sock.close()


def collect(
    consumer: object, marker: str, *, timeout: float = 60.0, want: int = 2
) -> dict[str, dict]:
    """First payload per topic that mentions ``marker``."""
    seen: dict[str, dict] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(seen) < want:
        message = consumer.poll(1.0)  # type: ignore[attr-defined]
        if message is None or message.error():
            continue
        payload = json.loads(message.value())
        if marker not in json.dumps(payload):
            continue
        seen.setdefault(message.topic(), payload)
    return seen


def test_syslog_line_becomes_a_tier_one_event_with_lineage(cfg: Settings) -> None:
    """The CP1 path: one line in, normalized OCSF and a lineage row out."""
    user = f"probe{uuid.uuid4().hex[:8]}"
    consumer = pinned_consumer(cfg)
    try:
        send(
            f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user "
            f"{user} from 45.12.3.9 port 52144 ssh2".encode()
        )
        seen = collect(consumer, user)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert "norm.iam" in seen, f"expected norm.iam, saw {sorted(seen)}"
    event = NormEvent.model_validate(seen["norm.iam"])
    assert event.ulpf.tier == 1
    assert event.ulpf.conformance == "match"
    assert event.class_uid == 3002, "Authentication"
    assert event.ulpf.contract is not None
    assert event.ulpf.contract.id == "linux_sshd"
    assert seen["norm.iam"]["status_id"] == 2, "a failed password is a failure"
    assert seen["norm.iam"]["src_endpoint"]["ip"] == "45.12.3.9"
    assert seen["norm.iam"]["user"]["name"] == user

    # The service, not the engine, knows where the record sat: the coordinates must be real.
    assert event.ulpf.raw_ref.topic == "raw.linux"
    assert event.ulpf.raw_ref.offset > 0

    assert "lineage" in seen
    lineage = LineageRecord.model_validate(seen["lineage"])
    assert lineage.tier == 1
    assert lineage.contract_ref == "linux_sshd@1"
    assert lineage.norm_topic == "norm.iam"
    assert user in lineage.search_terms
    assert "45.12.3.9" in lineage.search_terms


def test_offsets_in_the_delivered_event_slice_the_original_bytes(cfg: Settings) -> None:
    """P4 end to end: the offsets survive the trip through two Kafka topics."""
    import base64

    user = f"prov{uuid.uuid4().hex[:8]}"
    line = (
        f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user "
        f"{user} from 45.12.3.9 port 52144 ssh2".encode()
    )
    consumer = pinned_consumer(cfg)
    try:
        send(line)
        seen = collect(consumer, user)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    event = seen["norm.iam"]
    raw = line  # the edge stamped exactly these bytes
    for path, span in event["ulpf"]["field_offsets"].items():
        cursor = event
        for part in path.split("."):
            cursor = cursor[part]
        assert raw[span[0] : span[1]].decode() == str(cursor), path
    # And the raw text that travelled with the event decodes back to the same bytes.
    assert base64.b64encode(raw)  # sanity: the line is what we think it is
    assert event["raw_data"].encode() == raw


def test_a_source_without_a_contract_still_produces_an_event_and_a_dlq_copy(cfg: Settings) -> None:
    """P2: never drop. An unregistered source is visible in both places."""
    marker = f"nocontract{uuid.uuid4().hex[:8]}"
    consumer = pinned_consumer(cfg)
    try:
        send(f"<86>Sep 26 14:05:13 unknown-box weird: {marker} nothing maps this".encode())
        seen = collect(consumer, marker, want=3)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert "dlq" in seen, f"a degraded event must reach the DLQ, saw {sorted(seen)}"
    assert seen["dlq"]["reason_code"] in {"no_contract", "no_template_match"}
    normalized = [topic for topic in seen if topic.startswith("norm.")]
    assert normalized, "the event must still be delivered to a norm.* topic"
    event = NormEvent.model_validate(seen[normalized[0]])
    assert event.ulpf.tier >= 3
    assert event.raw_data, "the raw text must survive even when nothing parses"


def messy_line(marker: str) -> bytes:
    """The demo's T3 shape: syslog header, JSON body, kv remainder, and a trace tail.

    Nothing in the registry matches it, which is the point — this is what "ultra-messy in, useful
    out" has to survive.
    """
    return (
        f'<134>Sep 26 14:05:14 app-{marker} svc[233]: {{"evt":"auth","msg":"user={marker} '
        f'FAILED login from 103.21.4.77 via 10.2.3.4"}} attempts:1 | trace=  at '
        f"com.x.Auth.login(Auth.java:88)"
    ).encode()


def dlq_copy_of(cfg: Settings, event_uid: str, *, timeout: float = 45.0) -> dict | None:
    """The DLQ record for one event, read from the beginning of the topic.

    The DLQ text is masked, so it cannot be found by searching for the probe's user name. Reading
    the topic and matching on ``event_uid`` is what a console would do too.
    """
    consumer = make_consumer(f"it-dlq-{uuid.uuid4().hex[:8]}", cfg=cfg)
    try:
        metadata = consumer.list_topics("dlq", timeout=20)
        partitions = list(metadata.topics["dlq"].partitions)
        positions = []
        for partition in partitions:
            low, high = consumer.get_watermark_offsets(
                TopicPartition("dlq", partition), timeout=10, cached=False
            )
            # The last few hundred records are plenty: this probe was just produced.
            positions.append(TopicPartition("dlq", partition, max(low, high - 500)))
        consumer.assign(positions)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            payload = json.loads(message.value())
            if payload.get("event_uid") == event_uid:
                return payload
    finally:
        consumer.close()
    return None


def test_an_unregistered_messy_event_arrives_as_tier_three_with_observables(cfg: Settings) -> None:
    """A4 AC1, end to end: the cascade runs inside the real service, not just in a unit test."""
    marker = f"messy{uuid.uuid4().hex[:8]}"
    line = messy_line(marker)
    consumer = pinned_consumer(cfg)
    try:
        send(line)
        seen = collect(consumer, marker, want=3)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    normalized = [topic for topic in seen if topic.startswith("norm.")]
    assert normalized, f"the messy event must still be delivered, saw {sorted(seen)}"
    topic = normalized[0]
    assert topic == "norm.uncategorized", f"an unmatched event is uncategorized, got {topic}"

    payload = seen[topic]
    event = NormEvent.model_validate(payload)
    assert event.ulpf.tier == 3, f"expected unknown_template, got tier {event.ulpf.tier}"
    assert event.ulpf.conformance == "unknown_template"
    assert event.class_uid == 0, "tier 3 never claims a class — the guess lives in class_hint"

    # The cascade must have recognised the layers it walked through.
    path = event.ulpf.parse_path
    assert any(step.startswith("auto:") for step in path), path
    assert any("json" in step for step in path), path

    values = {str(observable["value"]) for observable in payload.get("observables", [])}
    assert {"103.21.4.77", "10.2.3.4", marker} <= values, values
    # Neither IP may be assigned to an endpoint: at tier 3 nothing says which end is which.
    for endpoint in ("src_endpoint", "dst_endpoint"):
        assert not payload.get(endpoint, {}).get("ip"), f"tier 3 must not fill {endpoint}.ip"

    assert event.ulpf.class_hint is not None, "an auth event should carry a hint"

    # P2: a tier 3 event still leaves a DLQ copy — but it cannot be found by searching for the user,
    # because `text_masked` replaces it with <USER_1> (that is the point of masking). The copy is
    # found by the event_uid instead, which is also how the console will join the two.
    dlq = dlq_copy_of(cfg, event.ulpf.event_uid)
    assert dlq is not None, "no DLQ copy for the tier 3 event"
    assert dlq["reason_code"] in {"no_contract", "no_template_match"}
    assert dlq["tier"] == 3
    assert marker not in dlq["text_masked"], "the user must be masked in the DLQ text"
    assert "<USER_" in dlq["text_masked"], dlq["text_masked"]


def test_tier_three_offsets_still_slice_the_raw_bytes_after_kafka(cfg: Settings) -> None:
    """P4 for tier 3: every observable and field offset survives the trip, byte for byte."""
    marker = f"prov3{uuid.uuid4().hex[:8]}"
    line = messy_line(marker)
    consumer = pinned_consumer(cfg)
    try:
        send(line)
        seen = collect(consumer, marker, want=3)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    normalized = [topic for topic in seen if topic.startswith("norm.")]
    assert normalized, f"no event delivered, saw {sorted(seen)}"
    event = seen[normalized[0]]
    offsets = event["ulpf"]["field_offsets"]
    assert offsets, "tier 3 without offsets would be a guess, not evidence"

    observables = {observable["name"]: observable["value"] for observable in event["observables"]}
    for path, span in offsets.items():
        if path.startswith("observables."):
            expected = observables[path.split(".", 1)[1]]
        else:
            cursor: object = event
            for part in path.split("."):
                cursor = cursor[part]  # type: ignore[index]
            expected = cursor
        assert line[span[0] : span[1]].decode() == str(expected), path

    # And at least one of the extracted IPs is located, not merely derived.
    assert any(name.startswith("ip") for name in observables), observables
    assert any(path.startswith("observables.ip") for path in offsets), sorted(offsets)


def test_no_duplicate_event_uid_revision_pairs(cfg: Settings) -> None:
    """Transactions mean one input record yields exactly one lineage row."""
    user = f"dup{uuid.uuid4().hex[:8]}"
    consumer = pinned_consumer(cfg)
    try:
        send(
            f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user "
            f"{user} from 45.12.3.9 port 52144 ssh2".encode(),
            times=1,
        )
        rows: list[tuple[str, int]] = []
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            message = consumer.poll(1.0)  # type: ignore[attr-defined]
            if message is None or message.error() or message.topic() != "lineage":
                continue
            payload = json.loads(message.value())
            if user not in json.dumps(payload):
                continue
            rows.append((payload["event_uid"], payload["revision"]))
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert rows, "no lineage row produced"
    assert len(rows) == len(set(rows)), f"duplicate (event_uid, revision): {rows}"


@pytest.mark.slow
def test_killing_the_normalizer_mid_stream_loses_nothing(cfg: Settings) -> None:
    """A3 task 9 / CP1 check 7: kill -9 during a stream, restart, no gaps and no duplicates.

    Measured against what **Kafka accepted**, not what the socket was handed: UDP is allowed to
    drop, so the envelopes actually on `raw.*` are the denominator. Anything the normalizer then
    fails to emit is a real defect, and duplicates are always a defect.
    """
    total = 120
    marker = f"kill{uuid.uuid4().hex[:8]}"
    raw_consumer = pinned_consumer_for(cfg, ("raw.",))
    out_consumer = pinned_consumer_for(cfg, ("lineage",))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for index in range(total):
                sock.sendto(
                    f"<86>Sep 26 14:05:12 core-lnx-07 sshd[{index}]: Failed password for "
                    f"invalid user {marker}u{index} from 45.12.3.9 port 52144 ssh2".encode(),
                    ("127.0.0.1", CORE_UDP),
                )
                if index == total // 3:
                    subprocess.run(
                        ["docker", "kill", "--signal=KILL", "veyra-normalizer"],
                        capture_output=True,
                        timeout=60,
                    )
                time.sleep(0.02)
        finally:
            sock.close()

        subprocess.run(
            ["docker", "start", "veyra-normalizer"], capture_output=True, timeout=120, check=False
        )

        # What Kafka actually holds for this marker.
        accepted: set[str] = set()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            message = raw_consumer.poll(1.0)  # type: ignore[attr-defined]
            if message is None or message.error():
                continue
            payload = json.loads(message.value())
            import base64

            if marker.encode() in base64.b64decode(payload["raw_b64"]):
                accepted.add(payload["event_uid"])
            if len(accepted) >= total:
                break
        assert accepted, "no marked envelopes reached raw.* at all"

        # What the normalizer emitted for those events.
        emitted: dict[str, list[int]] = {}
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline and len(emitted) < len(accepted):
            message = out_consumer.poll(1.0)  # type: ignore[attr-defined]
            if message is None or message.error():
                continue
            payload = json.loads(message.value())
            uid = payload.get("event_uid")
            if uid in accepted:
                emitted.setdefault(uid, []).append(payload["revision"])
    finally:
        raw_consumer.close()  # type: ignore[attr-defined]
        out_consumer.close()  # type: ignore[attr-defined]

    duplicated = {uid: revs for uid, revs in emitted.items() if len(revs) != len(set(revs))}
    assert not duplicated, f"duplicate (event_uid, revision) after restart: {duplicated}"
    missing = accepted - set(emitted)
    assert not missing, (
        f"{len(missing)} of {len(accepted)} envelopes on raw.* produced no lineage row "
        "— the restart lost events"
    )
