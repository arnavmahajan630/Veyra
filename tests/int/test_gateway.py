"""A2 integration tests: the gateway against the real broker.

Needs `make up SERVICES="a2"` (plus `c1`, or `tools/mock_control_publish.py --api-key`, so the
pepper and at least one key exist). These cover the acceptance criteria that only real
infrastructure can prove:

* AC1 — a key holder's event reaches `raw.custom` with the right source, tenant and `auth` block;
* AC2 — revoking the key in `control` closes the door within 2 s, with no restart;
* AC3 — a 5 EPS quota really bounds a sender, and the metric says so;
* AC4 — a real Vector `splunk_hec_logs` client delivers 1000 events (`slow`);
* AC5 — with Kafka down, requests are 503 and never 200, and nothing is lost after a retry (`slow`).

Keys are published straight to `control` here rather than through control-api, so a run is
deterministic and does not depend on C1's HTTP surface being up. The message shape is the one C1
publishes (`control_api.messages.apikey_message`).
"""

from __future__ import annotations

import base64
import json
import secrets
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
from confluent_kafka import TopicPartition

from veyra_common.hashing import sha256_hex
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import ApiKeyMessage, Envelope, control_key
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_CONTROL

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
GATEWAY = "http://localhost:8088"
SOURCE_ID = "src_authsrv_01"
TENANT = "t_maha_power"
T3_EVENT = (
    '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login from '
    '103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


@pytest.fixture(scope="module", autouse=True)
def require_gateway() -> None:
    running = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    ).stdout
    if "veyra-ingest-gateway" not in running:
        pytest.skip('ingest-gateway is not running — start it with: make up SERVICES="a2"')


@pytest.fixture(scope="module")
def pepper() -> bytes:
    """The pepper control-api created. Without it the gateway cannot authenticate anyone."""
    path = REPO / "data" / "keys" / "api_pepper"
    if not path.exists():
        pytest.skip(
            "data/keys/api_pepper does not exist — run control-api "
            "(make up SERVICES=c1) or tools/mock_control_publish.py --api-key"
        )
    return path.read_bytes().strip()


def issue_key(cfg: Settings, pepper: bytes, *, quota_eps: int = 500) -> tuple[str, str]:
    """Publish an `apikey:` message the way control-api does. Returns ``(key_id, secret)``."""
    key_id = f"k_it{uuid.uuid4().hex[:8]}"
    secret = f"veyra_{secrets.token_urlsafe(24)}"
    message = ApiKeyMessage(
        key_id=key_id,
        secret_sha256=sha256_hex(pepper + secret.encode()),
        pepper_id="p_it",
        source_id=SOURCE_ID,
        tenant_id=TENANT,
        status="active",
        quota_eps=quota_eps,
        created_at="2026-09-28T10:00:00.000000000Z",
    )
    publish(cfg, key_id, message.model_dump(mode="json"))
    wait_for_key(secret)
    return key_id, secret


def revoke_key(cfg: Settings, key_id: str, pepper: bytes, secret: str) -> None:
    message = ApiKeyMessage(
        key_id=key_id,
        secret_sha256=sha256_hex(pepper + secret.encode()),
        pepper_id="p_it",
        source_id=SOURCE_ID,
        tenant_id=TENANT,
        status="revoked",
        quota_eps=0,
        created_at="2026-09-28T10:00:00.000000000Z",
    )
    publish(cfg, key_id, message.model_dump(mode="json"))


def publish(cfg: Settings, key_id: str, payload: dict) -> None:
    producer = make_producer(cfg=cfg)
    producer.produce(
        TOPIC_CONTROL, key=control_key("apikey", key_id), value=json.dumps(payload).encode()
    )
    assert producer.flush(20) == 0, "the control message was not accepted"


def wait_for_key(secret: str, *, timeout: float = 20.0) -> None:
    """Block until the gateway has the key — it follows `control`, so this takes a moment."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = post_event(secret, "warmup probe")
        if response.status_code == 200:
            return
        time.sleep(0.25)
    raise AssertionError(f"the gateway never accepted the new key (last: {response.status_code})")


def post_event(secret: str, line: str) -> httpx.Response:
    return httpx.post(
        f"{GATEWAY}/services/collector/event",
        content=json.dumps({"event": line}),
        headers={"Authorization": f"Splunk {secret}"},
        timeout=30,
    )


def pinned_consumer(cfg: Settings, prefixes: tuple[str, ...] = ("raw.",)) -> object:
    """A consumer pinned to the current end of the raw topics, so old events cannot match."""
    consumer = make_consumer(f"it-gw-{uuid.uuid4().hex[:8]}", cfg=cfg)
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


def collect_envelopes(consumer: object, marker: str, *, timeout: float = 45.0) -> list[Envelope]:
    """Every envelope whose raw bytes contain ``marker``."""
    found: list[Envelope] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)  # type: ignore[attr-defined]
        if message is None or message.error():
            continue
        try:
            envelope = Envelope.model_validate_json(message.value())
        except Exception:
            continue
        if marker.encode() in base64.b64decode(envelope.raw_b64):
            found.append(envelope)
    return found


def metric(name: str) -> float:
    """One Prometheus counter's total across all label sets."""
    text = httpx.get(f"{GATEWAY}/metrics", timeout=20).text
    total = 0.0
    for line in text.splitlines():
        if line.startswith(f"{name}{{") or line.startswith(f"{name} "):
            total += float(line.rsplit(" ", 1)[1])
    return total


# ---------------------------------------------------------------- AC1
def test_a_key_holder_can_push_the_t3_event(cfg: Settings, pepper: bytes) -> None:
    """AC1: valid key → one envelope on raw.custom, attributed to the onboarded source."""
    marker = f"gw{uuid.uuid4().hex[:8]}"
    _, secret = issue_key(cfg, pepper)
    consumer = pinned_consumer(cfg)
    try:
        response = httpx.post(
            f"{GATEWAY}/services/collector/event",
            content=json.dumps({"event": T3_EVENT.replace("a.sharma", marker), "host": "authsrv"}),
            headers={"Authorization": f"Splunk {secret}"},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "Success"
        envelopes = collect_envelopes(consumer, marker)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert len(envelopes) == 1, f"expected exactly one envelope, got {len(envelopes)}"
    envelope = envelopes[0]
    assert envelope.source_id == SOURCE_ID
    assert envelope.tenant_id == TENANT
    assert envelope.vendor == "custom"
    assert envelope.transport == "http_hec_event"
    assert envelope.auth.method == "api_key"
    assert envelope.auth.key_id, "the envelope must name the key that authenticated it"
    assert envelope.custody == "realtime"
    assert envelope.hash_matches(), "the seal must cover the bytes actually stored"
    assert envelope.hec_meta is not None and envelope.hec_meta.host == "authsrv"
    # The multi-line event survives as one event, newline and all.
    assert b"\n  at com.x.Auth.login" in envelope.raw_bytes


def test_the_raw_endpoint_splits_lines_the_way_the_edge_does(cfg: Settings, pepper: bytes) -> None:
    marker = f"raw{uuid.uuid4().hex[:8]}"
    _, secret = issue_key(cfg, pepper)
    body = f"first {marker} line\n  at continuation.of(first)\nsecond {marker} line\n"
    consumer = pinned_consumer(cfg)
    try:
        response = httpx.post(
            f"{GATEWAY}/services/collector/raw",
            content=body.encode(),
            headers={"Authorization": f"Splunk {secret}"},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        envelopes = collect_envelopes(consumer, marker)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert len(envelopes) == 2
    joined = next(e for e in envelopes if e.framing.parts == 2)
    assert joined.framing.method == "multiline_join"
    assert b"\n  at continuation.of(first)" in joined.raw_bytes
    assert {e.transport for e in envelopes} == {"http_hec_raw"}


def test_a_batch_upload_is_marked_post_hoc(cfg: Settings, pepper: bytes) -> None:
    marker = f"bat{uuid.uuid4().hex[:8]}"
    _, secret = issue_key(cfg, pepper)
    payload = "".join(f"line {index} {marker}\n" for index in range(5)).encode()
    consumer = pinned_consumer(cfg)
    try:
        response = httpx.post(
            f"{GATEWAY}/v1/batch",
            files={"file": ("history.log", payload, "text/plain")},
            headers={"Authorization": f"Splunk {secret}"},
            timeout=30,
        )
        assert response.status_code == 200, response.text
        manifest = response.json()["manifest"]
        envelopes = collect_envelopes(consumer, marker)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert manifest["count"] == 5
    assert manifest["sha256_of_file"] == sha256_hex(payload)
    assert len(envelopes) == 5
    assert {e.custody for e in envelopes} == {"post_hoc"}
    assert {e.transport for e in envelopes} == {"http_batch"}
    uids = {e.event_uid for e in envelopes}
    assert manifest["first_event_uid"] in uids and manifest["last_event_uid"] in uids


# ---------------------------------------------------------------- AC2
def test_revoking_a_key_closes_the_door_within_two_seconds(cfg: Settings, pepper: bytes) -> None:
    """AC2. The budget is generous by design: what is being proven is that no restart is needed."""
    key_id, secret = issue_key(cfg, pepper)
    assert post_event(secret, "before revocation").status_code == 200

    revoke_key(cfg, key_id, pepper, secret)
    started = time.monotonic()
    while time.monotonic() - started < 10:
        response = post_event(secret, "after revocation")
        if response.status_code == 401:
            elapsed = time.monotonic() - started
            assert elapsed <= 2.0, f"revocation took {elapsed:.2f}s, AC2 allows 2s"
            assert response.json() == {"text": "Invalid authorization", "code": 3}
            return
        time.sleep(0.1)
    raise AssertionError("the revoked key still worked after 10s")


# ---------------------------------------------------------------- AC3
def test_a_quota_of_five_eps_throttles_a_burst_of_fifty(cfg: Settings, pepper: bytes) -> None:
    """AC3: about 5 accepted, the rest 429, and the metric moves."""
    _, secret = issue_key(cfg, pepper, quota_eps=5)
    before = metric("veyra_gateway_throttled_total")

    # The warm-up probe in issue_key already spent a token, so refill first.
    time.sleep(1.5)
    codes: list[int] = []
    started = time.monotonic()
    for index in range(50):
        codes.append(post_event(secret, f"burst {index}").status_code)
    elapsed = time.monotonic() - started

    accepted = codes.count(200)
    throttled = codes.count(429)
    assert accepted + throttled == 50, f"unexpected statuses: {set(codes)}"
    assert throttled > 0, "a 5 EPS quota must refuse part of a 50-event burst"
    # One second of bucket plus whatever refilled while the burst was in flight.
    allowed = 5 + int(elapsed * 5) + 2
    assert accepted <= allowed, f"{accepted} accepted in {elapsed:.2f}s, expected <= {allowed}"
    assert metric("veyra_gateway_throttled_total") > before


def test_an_unknown_key_is_refused_and_counted(cfg: Settings, pepper: bytes) -> None:
    response = post_event("veyra_not-a-real-key", "should not arrive")
    assert response.status_code == 401
    assert response.json()["code"] == 3


def test_healthz_reports_ready(cfg: Settings) -> None:
    response = httpx.get(f"{GATEWAY}/healthz", timeout=20)
    assert response.status_code == 200, response.text
    assert response.json().get("ready") in (True, 1), response.text


# ---------------------------------------------------------------- AC5
@pytest.mark.slow
def test_with_kafka_down_requests_are_503_and_nothing_is_lost(cfg: Settings, pepper: bytes) -> None:
    """AC5. The property is the 503: a client that is told 200 must never have to wonder."""
    marker = f"down{uuid.uuid4().hex[:8]}"
    _, secret = issue_key(cfg, pepper)
    subprocess.run(["docker", "stop", "veyra-kafka"], capture_output=True, timeout=120, check=False)
    try:
        response = post_event(secret, f"while down {marker}")
        assert response.status_code == 503, f"got {response.status_code}: {response.text}"
        assert response.json()["code"] == 9
    finally:
        subprocess.run(
            ["docker", "start", "veyra-kafka"], capture_output=True, timeout=180, check=False
        )

    # The client retries what it was never told was accepted, and now it lands.
    deadline = time.monotonic() + 180
    consumer = None
    while time.monotonic() < deadline:
        try:
            consumer = pinned_consumer(cfg)
            break
        except Exception:
            time.sleep(5)
    assert consumer is not None, "kafka did not come back"
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            retry = post_event(secret, f"after retry {marker}")
            if retry.status_code == 200:
                break
            time.sleep(2)
        assert retry.status_code == 200, retry.text
        envelopes = collect_envelopes(consumer, marker, timeout=60)
    finally:
        consumer.close()  # type: ignore[attr-defined]

    assert envelopes, "the retried event never reached raw.*"
    assert all(e.hash_matches() for e in envelopes)


# ---------------------------------------------------------------- AC4
@pytest.mark.slow
def test_a_real_vector_hec_client_delivers_a_thousand_events(cfg: Settings, pepper: bytes) -> None:
    """AC4: the "point your existing shipper here" claim, tested with the pinned Vector image."""
    marker = f"vec{uuid.uuid4().hex[:8]}"
    _, secret = issue_key(cfg, pepper, quota_eps=20_000)
    config = f"""
[sources.demo]
type = "demo_logs"
format = "shuffle"
lines = ["{marker} event from vector"]
count = 1000
interval = 0.0

[sinks.veyra]
type = "splunk_hec_logs"
inputs = ["demo"]
endpoint = "http://ingest-gateway:8088"
default_token = "{secret}"
compression = "none"
encoding.codec = "text"
"""
    config_path = REPO / "data" / f"vector-hec-{marker}.toml"
    config_path.write_text(config)
    consumer = pinned_consumer(cfg)
    try:
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "veyra_net",
            "-v",
            f"{config_path}:/etc/vector/vector.toml:ro",
            "timberio/vector:0.58.0-debian",
            "--config",
            "/etc/vector/vector.toml",
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
        assert "Sink health check ok" in result.stderr or result.returncode == 0, result.stderr[
            -2000:
        ]
        envelopes = collect_envelopes(consumer, marker, timeout=120)
    finally:
        consumer.close()  # type: ignore[attr-defined]
        config_path.unlink(missing_ok=True)

    assert len(envelopes) == 1000, f"Vector's 1000 events produced {len(envelopes)} envelopes"
    assert {e.source_id for e in envelopes} == {SOURCE_ID}
    assert all(e.auth.method == "api_key" for e in envelopes)
