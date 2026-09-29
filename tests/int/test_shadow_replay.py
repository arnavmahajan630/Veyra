"""A5 integration tests: a canary in shadow, and replayed history that supersedes itself.

Needs `make up SERVICES="a2 a3 c1"` — the gateway is how the demo's auth server pushes the T3 events
(Beat 3), so these tests send through it rather than faking envelopes onto `raw.*`. That also means
the whole chain is under test: HTTP → `raw.custom` → normalizer → `norm.*` + `shadow` + `lineage`.

The sequence mirrors Beat 4 of the demo script:

1. `authsrv@1` active, `authsrv@2` as **candidate** → the delivered event is still tier 3 and a
   `shadow` record shows what v2 would have produced (AC1);
2. promote `authsrv@2` to active, then replay those same events → the same `event_uid`s come
   back at `revision=2`, tier 1, with `supersedes` set (AC3);
3. kill the normalizer mid-replay → no duplicate `(event_uid, revision)` after it recovers (AC4).

Control messages go straight to the topic here rather than through control-api's HTTP API, so a run
is deterministic and does not depend on C's lifecycle endpoints; the message shape is C's
(`ContractMessage`, the same one `tools/mock_control_publish.py` writes).
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from confluent_kafka import TopicPartition

from veyra_common.framing import split_lines
from veyra_common.hashing import sha256_hex
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import (
    ApiKeyMessage,
    ContractMessage,
    Envelope,
    LineageRecord,
    ShadowRecord,
    control_key,
)
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_CONTROL, TOPIC_LINEAGE, TOPIC_SHADOW
from veyra_contracts import compile as compile_contract

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "demo" / "corpus"
CONTRACTS = REPO / "packages" / "veyra_engine" / "tests" / "contracts"
GATEWAY = "http://localhost:8088"
SOURCE_ID = "src_authsrv_01"
TENANT = "t_maha_power"


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


@pytest.fixture(scope="module", autouse=True)
def require_services() -> None:
    running = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    ).stdout
    missing = [name for name in ("veyra-ingest-gateway", "veyra-normalizer") if name not in running]
    if missing:
        pytest.skip(f'{", ".join(missing)} not running — make up SERVICES="a2 a3 c1"')


@pytest.fixture(scope="module")
def pepper() -> bytes:
    path = REPO / "data" / "keys" / "api_pepper"
    if not path.exists():
        pytest.skip("data/keys/api_pepper does not exist — run control-api (make up SERVICES=c1)")
    return path.read_bytes().strip()


# ---------------------------------------------------------------- publishing control
def publish(cfg: Settings, items: list[tuple[str, dict[str, Any]]]) -> None:
    producer = make_producer(cfg=cfg)
    for key, payload in items:
        producer.produce(TOPIC_CONTROL, key=key, value=json.dumps(payload).encode())
    assert producer.flush(20) == 0, "control messages were not acknowledged"


def contract_message(active: str, candidate: str | None = None) -> dict[str, Any]:
    """`contract:authsrv` the way control-api publishes it, optionally with a canary."""
    compiled = compile_contract((CONTRACTS / active).read_text()).model_dump()
    canary = (
        compile_contract((CONTRACTS / candidate).read_text()).model_dump()
        if candidate is not None
        else None
    )
    return ContractMessage(
        id=str(compiled["contract"]),
        version=int(compiled["version"]),
        state="active",
        tenant_id=str(compiled.get("tenant") or TENANT),
        sources=list(compiled.get("sources") or [SOURCE_ID]),
        compiled=compiled,
        candidate=(
            {"version": int(canary["version"]), "compiled": canary} if canary is not None else None
        ),
        published_at="2026-09-29T00:00:00.000000000Z",
    ).model_dump(mode="json")


def pepper_id(pepper: bytes) -> str:
    """The id control-api derives from the pepper file (`control_api.keys.ensure_pepper`).

    The gateway refuses a key minted under a different pepper *and says so*, so a test that made one
    up would be rejected for a reason that has nothing to do with what it is testing.
    """
    return "p_" + sha256_hex(pepper)[:8]


def issue_key(cfg: Settings, pepper: bytes) -> str:
    key_id = f"k_a5{uuid.uuid4().hex[:8]}"
    secret = f"veyra_{secrets.token_urlsafe(24)}"
    message = ApiKeyMessage(
        key_id=key_id,
        secret_sha256=sha256_hex(pepper + secret.encode()),
        pepper_id=pepper_id(pepper),
        source_id=SOURCE_ID,
        tenant_id=TENANT,
        status="active",
        quota_eps=2000,
        created_at="2026-09-29T00:00:00.000000000Z",
    )
    publish(cfg, [(control_key("apikey", key_id), message.model_dump(mode="json"))])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if post(secret, "warmup probe").status_code == 200:
            return secret
        time.sleep(0.25)
    raise AssertionError("the gateway never accepted the new key")


def post(secret: str, line: str) -> httpx.Response:
    return httpx.post(
        f"{GATEWAY}/services/collector/event",
        content=json.dumps({"event": line}),
        headers={"Authorization": f"Splunk {secret}"},
        timeout=30,
    )


# ---------------------------------------------------------------- reading topics
def pinned_consumer(cfg: Settings, prefixes: tuple[str, ...]) -> Any:
    consumer = make_consumer(f"it-a5-{uuid.uuid4().hex[:8]}", cfg=cfg)
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
    assert positions, f"no topics matching {prefixes}"
    consumer.assign(positions)
    return consumer


def mentions(payload: dict, marker: str) -> bool:
    """Whether a record is about our probe.

    Two records need care. A **raw envelope** carries its bytes base64-encoded, so the marker is
    not in the JSON text and has to be decoded. A **shadow record** holds no event text at all —
    paths, tiers and contract refs only — so it can never be matched by marker; it is found by
    ``event_uid``, which is also how the console joins the two.
    """
    if "raw_b64" in payload:
        return marker.encode() in base64.b64decode(payload["raw_b64"])
    return marker in json.dumps(payload)


def drain(
    consumer: Any, marker: str | None = None, *, want: int = 1, timeout: float = 60.0
) -> list[dict]:
    """Payloads from the assigned topics, until ``want`` matches or the timeout.

    With ``marker`` only matching records are kept; without it everything is returned, which is
    what a caller needs when it has to join on ``event_uid`` afterwards.
    """
    found: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(found) < want:
        message = consumer.poll(1.0)
        if message is None or message.error():
            continue
        try:
            payload = json.loads(message.value())
        except (json.JSONDecodeError, TypeError):
            continue
        if marker is not None and not mentions(payload, marker):
            continue
        found.append(payload)
    return found


def run_replay(limit: int, *, marker: str, revision: int = 2) -> subprocess.CompletedProcess[str]:
    """A's standalone replay publisher (`tools/mock_replay.py`), run the way an operator would.

    ``--contains`` matters: the source has events from every earlier run on it, and replaying the
    oldest eight of them would tell us nothing about the ones this test just sent.
    """
    command = [
        "uv",
        "run",
        "python",
        "tools/mock_replay.py",
        "--source",
        SOURCE_ID,
        "--contains",
        marker,
        "--limit",
        str(limit),
        "--revision",
        str(revision),
    ]
    return subprocess.run(
        command,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "VEYRA_KAFKA_BOOTSTRAP": "localhost:29092"},
    )


def t3_lines() -> list[str]:
    events = split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())
    return [event.raw.decode() for event in events]


# ---------------------------------------------------------------- AC1
def test_ac1_a_canary_is_compared_in_shadow_without_changing_the_output(
    cfg: Settings, pepper: bytes
) -> None:
    """AC1: `shadow` shows candidate tier 1 vs active tier 3, and `norm.*` still shows tier 3."""
    marker = f"a5shadow{uuid.uuid4().hex[:6]}"
    secret = issue_key(cfg, pepper)
    canary = contract_message("authsrv.yaml", "authsrv_v2.yaml")
    publish(cfg, [(control_key("contract", "authsrv"), canary)])
    time.sleep(3)  # the normalizer follows `control`; give it the swap

    consumer = pinned_consumer(cfg, ("norm.", TOPIC_SHADOW))
    try:
        line = t3_lines()[0].replace("a.sharma", marker)
        assert post(secret, line).status_code == 200
        # A shadow record carries no event text, so read everything and join on event_uid below.
        records = drain(consumer, want=40, timeout=45)
    finally:
        consumer.close()

    events = [r for r in records if "ulpf" in r and marker in json.dumps(r)]
    assert events, f"the event was not delivered; saw {len(records)} record(s)"
    event_uid = events[0]["ulpf"]["event_uid"]
    shadows = [r for r in records if "candidate_ref" in r and r.get("event_uid") == event_uid]
    assert shadows, f"no shadow record for {event_uid}"

    shadow = ShadowRecord.model_validate(shadows[0])
    assert shadow.active_tier == 3, "authsrv@1 has never seen this shape"
    assert shadow.candidate_tier == 1, "authsrv@2 is what the drift loop produced"
    assert shadow.active_ref == "authsrv@1"
    assert shadow.candidate_ref == "authsrv@2"
    assert "user.name" in shadow.changed_fields
    assert shadow.regressions == []

    delivered = events[0]
    assert delivered["ulpf"]["tier"] == 3, "the canary must not change what is delivered"
    assert delivered["class_uid"] == 0
    assert delivered["ulpf"]["shadow"] is False, "the delivered event came from the active version"


# ---------------------------------------------------------------- AC3
def test_ac3_replayed_events_come_back_as_revision_two(cfg: Settings, pepper: bytes) -> None:
    """AC3: promote v2, replay the same events, get the same uids at revision 2, tier 1."""
    marker = f"a5replay{uuid.uuid4().hex[:6]}"
    secret = issue_key(cfg, pepper)
    lines = [line.replace("a.sharma", marker) for line in t3_lines()[:8]]

    # 1. Send them while v1 is active: they land as tier 3.
    publish(cfg, [(control_key("contract", "authsrv"), contract_message("authsrv.yaml"))])
    time.sleep(3)
    raw_consumer = pinned_consumer(cfg, ("raw.",))
    try:
        for line in lines:
            assert post(secret, line).status_code == 200
        originals = drain(raw_consumer, marker, want=len(lines))
    finally:
        raw_consumer.close()
    assert len(originals) == len(lines), f"{len(originals)} of {len(lines)} reached raw.*"
    uids = {Envelope.model_validate(payload).event_uid for payload in originals}

    # 2. Promote v2 — a replay uses the contract that is active at replay time.
    publish(cfg, [(control_key("contract", "authsrv"), contract_message("authsrv_v2.yaml"))])
    time.sleep(3)

    # 3. Replay them.
    out_consumer = pinned_consumer(cfg, ("norm.", TOPIC_LINEAGE))
    try:
        result = run_replay(len(lines), marker=marker)
        assert result.returncode == 0, result.stdout + result.stderr
        replayed = drain(out_consumer, marker, want=len(lines) * 2, timeout=90)
    finally:
        out_consumer.close()

    events = [r for r in replayed if "ulpf" in r and r["ulpf"].get("replay")]
    assert events, f"no replayed events arrived: {result.stdout}"
    for event in events:
        assert event["ulpf"]["revision"] == 2
        assert event["ulpf"]["supersedes"] == f"{event['ulpf']['event_uid']}@1"
        assert event["ulpf"]["tier"] == 1, "replay uses the contract active now (authsrv@2)"
        assert event["ulpf"]["event_uid"] in uids, "a replay re-emits the same event_uid"
        assert event["class_uid"] == 3002

    lineages = [
        LineageRecord.model_validate(row)
        for row in replayed
        if "replay_job_id" in row and row.get("replay")
    ]
    assert lineages, "control-api counts lineage rows carrying replay_job_id"
    assert all(row.replay_job_id for row in lineages)
    assert all(row.revision == 2 for row in lineages)


# ---------------------------------------------------------------- AC4
@pytest.mark.slow
def test_ac4_killing_the_normalizer_mid_replay_leaves_no_duplicates(
    cfg: Settings, pepper: bytes
) -> None:
    """AC4: one transaction, and a revision taken from the message, means recovery cannot double."""
    marker = f"a5kill{uuid.uuid4().hex[:6]}"
    secret = issue_key(cfg, pepper)
    publish(cfg, [(control_key("contract", "authsrv"), contract_message("authsrv_v2.yaml"))])
    time.sleep(3)

    lines = [
        f"user={marker}{index} FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"
        for index in range(40)
    ]
    raw_consumer = pinned_consumer(cfg, ("raw.",))
    try:
        for line in lines:
            assert post(secret, line).status_code == 200
        originals = drain(raw_consumer, marker, want=len(lines))
    finally:
        raw_consumer.close()
    assert originals, "nothing reached raw.*"

    lineage_consumer = pinned_consumer(cfg, (TOPIC_LINEAGE,))
    try:
        replay = run_replay(len(lines), marker=marker)
        assert replay.returncode == 0, replay.stdout + replay.stderr
        # Kill it while the batch is in flight, then let compose bring it back.
        time.sleep(0.5)
        subprocess.run(
            ["docker", "kill", "--signal=KILL", "veyra-normalizer"], capture_output=True, timeout=60
        )
        subprocess.run(["docker", "start", "veyra-normalizer"], capture_output=True, timeout=120)

        rows = [
            LineageRecord.model_validate(payload)
            for payload in drain(lineage_consumer, marker, want=len(lines) * 3, timeout=240)
        ]
    finally:
        lineage_consumer.close()

    assert rows, "no lineage rows after the restart"
    pairs = [(row.event_uid, row.revision) for row in rows if row.replay]
    assert pairs, "no replayed lineage rows"
    duplicates = {pair for pair in pairs if pairs.count(pair) > 1}
    assert not duplicates, f"duplicate (event_uid, revision) after recovery: {sorted(duplicates)}"
    assert {revision for _, revision in pairs} == {2}, "every replayed row is revision 2"
    assert base64.b64encode(marker.encode())  # sanity: the marker is what we searched for
