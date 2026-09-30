"""A6 integration tests: the router against the real broker, sinks and receipts.

Needs `make up SERVICES="a3 a6 c1"`. These cover the parts only a real stack can show:

* an event on `norm.*` becomes a line in the file Wazuh follows, and a `delivered` receipt;
* the partner route's copy of the same event has the user HMAC'd and the raw bytes redacted, and
  another tenant's events are absent from it (AC3);
* with Wazuh stopped for two minutes nothing is lost, every event is receipted `delivered`, and
  the breaker state is visible in metrics (AC4).

Events are published directly as IF-NORM-EVENT records, in the shape the engine emits them, so the
router is tested without depending on the normalizer's timing.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from confluent_kafka import TopicPartition

from veyra_common.envelope import rfc3339_ns
from veyra_common.hashing import sha256_hex, template_sig
from veyra_common.ids import uuid7_str
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import Receipt
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_RECEIPTS, norm_topic

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
WAZUH_SINK = REPO / "data" / "sinks" / "wazuh" / "veyra.ndjson"
PARTNER_SINK = REPO / "data" / "sinks" / "partner" / "partner.ndjson"
METRICS = "http://localhost:8202/metrics"
RECEIVED = "2026-09-26T14:10:00.000000000Z"


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


@pytest.fixture(scope="module", autouse=True)
def require_router() -> None:
    running = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    ).stdout
    if "veyra-router" not in running:
        pytest.skip('router is not running — make up SERVICES="a3 a6 c1"')


# ---------------------------------------------------------------- publishing
def norm_event(
    *,
    marker: str,
    tier: int = 1,
    tenant: str = "t_maha_power",
    source: str = "src_authsrv_01",
    class_uid: int = 3002,
    revision: int = 1,
    src_ip: str = "103.21.4.77",
) -> dict[str, Any]:
    """One IF-NORM-EVENT as the engine emits it, with a unique marker in the user name."""
    raw = f"<134>Sep 26 14:05:11 fw01 app[233]: user={marker} FAILED login from {src_ip}"
    event_uid = uuid7_str()
    event: dict[str, Any] = {
        "class_uid": class_uid if tier == 1 else 0,
        "category_uid": 3 if (tier == 1 and class_uid == 3002) else 0,
        "type_uid": class_uid * 100 + 1 if tier == 1 else 99,
        "activity_id": 1 if tier == 1 else 99,
        "severity_id": 3,
        "status_id": 2,
        "time": 1790000000000,
        "message": f"user={marker} FAILED login from {src_ip}",
        "raw_data": raw,
        "observables": [{"name": "user", "type_id": 4, "value": marker}],
        "unmapped": {},
        "metadata": {"version": "1.9.0", "product": {"name": "VEYRA", "vendor_name": "NTRO"}},
        "ulpf": {
            "v": 1,
            "event_uid": event_uid,
            "tenant_id": tenant,
            "source_id": source,
            "vendor": "custom",
            "zone": "dmz",
            "tier": tier,
            "conformance": "match" if tier == 1 else "unknown_template",
            "contract": {"id": "authsrv", "version": 2} if tier == 1 else None,
            "template": {
                "sig": template_sig("authsrv" if tier == 1 else "unregistered", raw),
                "id": "auth_failed" if tier == 1 else None,
            },
            "revision": revision,
            "supersedes": None if revision == 1 else f"{event_uid}@{revision - 1}",
            "replay": revision > 1,
            "received_time": RECEIVED,
            "raw_sha256": sha256_hex(raw.encode()),
            "raw_ref": {"topic": "raw.custom", "partition": 0, "offset": 1},
            "produced_at": rfc3339_ns(),
        },
    }
    if tier == 1:
        event["user"] = {"name": marker}
        event["src_endpoint"] = {"ip": src_ip}
    return event


def publish(cfg: Settings, events: list[dict[str, Any]]) -> None:
    producer = make_producer(cfg=cfg)
    for event in events:
        category = "iam" if event["class_uid"] == 3002 else "uncategorized"
        producer.produce(
            norm_topic(category),
            key=event["ulpf"]["event_uid"],
            value=json.dumps(event).encode(),
        )
    assert producer.flush(20) == 0, "norm events were not accepted"


# ---------------------------------------------------------------- reading back
def sink_lines(path: Path, marker: str, *, timeout: float = 60.0) -> list[dict[str, Any]]:
    """Lines mentioning ``marker`` in a sink file, waiting for the router to write them."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            found = [
                json.loads(line)
                for line in path.read_text(errors="replace").splitlines()
                if marker in line
            ]
            if found:
                return found
        time.sleep(0.5)
    return []


def receipts_for(cfg: Settings, event_uids: set[str], *, timeout: float = 60.0) -> list[Receipt]:
    """Every receipt for these events, read from the tail of the topic."""
    consumer = make_consumer(f"it-rcpt-{uuid.uuid4().hex[:8]}", cfg=cfg)
    found: list[Receipt] = []
    try:
        metadata = consumer.list_topics(TOPIC_RECEIPTS, timeout=20)
        positions = []
        for partition in metadata.topics[TOPIC_RECEIPTS].partitions:
            low, high = consumer.get_watermark_offsets(
                TopicPartition(TOPIC_RECEIPTS, partition), timeout=10, cached=False
            )
            positions.append(TopicPartition(TOPIC_RECEIPTS, partition, max(low, high - 2000)))
        consumer.assign(positions)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            receipt = Receipt.model_validate_json(message.value())
            if receipt.event_uid in event_uids:
                found.append(receipt)
    finally:
        consumer.close()
    return found


def metric(name: str) -> dict[str, float]:
    """One metric family as ``{labels: value}``."""
    text = httpx.get(METRICS, timeout=20).text
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith(f"{name}{{"):
            labels, _, value = line.rpartition(" ")
            out[labels[len(name) :]] = float(value)
    return out


# ---------------------------------------------------------------- the happy path
def test_an_event_reaches_the_wazuh_sink_with_its_veyra_block(cfg: Settings) -> None:
    marker = f"rt{uuid.uuid4().hex[:8]}"
    event = norm_event(marker=marker, tenant="t_ntro_core", source="src_lnx_core_07")
    publish(cfg, [event])

    lines = sink_lines(WAZUH_SINK, marker)
    assert lines, f"nothing with {marker} reached {WAZUH_SINK}"
    line = lines[0]
    assert line["veyra"]["tier"] == 1
    assert line["veyra"]["tenant"] == "t_ntro_core"
    assert line["veyra"]["src_ip"] == "103.21.4.77", "the flat field the brute-force rule needs"
    # The event itself is untouched — Wazuh reads IF-NORM-EVENT, not a VEYRA dialect.
    assert line["class_uid"] == 3002
    assert line["ulpf"]["tier"] == 1

    receipts = receipts_for(cfg, {event["ulpf"]["event_uid"]})
    statuses = {(receipt.route_id, receipt.status) for receipt in receipts}
    assert ("wazuh_main", "delivered") in statuses, statuses


def test_every_tier_is_delivered_including_the_unparseable(cfg: Settings) -> None:
    """P2 at the far end: a tier-4 event must still arrive somewhere an analyst looks."""
    marker = f"tiers{uuid.uuid4().hex[:6]}"
    events = [norm_event(marker=f"{marker}t{tier}", tier=tier) for tier in (1, 3, 4)]
    publish(cfg, events)
    for tier in (1, 3, 4):
        lines = sink_lines(WAZUH_SINK, f"{marker}t{tier}")
        assert lines, f"tier {tier} did not reach the sink"
        assert lines[0]["veyra"]["tier"] == tier


# ---------------------------------------------------------------- AC3
def test_ac3_the_partner_feed_is_masked_and_scoped(cfg: Settings) -> None:
    """The partner sees Maha Power's auth events with the identity HMAC'd, and nothing of NTRO's."""
    mine = f"maha{uuid.uuid4().hex[:8]}"
    theirs = f"ntro{uuid.uuid4().hex[:8]}"
    publish(
        cfg,
        [
            norm_event(marker=mine, tenant="t_maha_power", source="src_authsrv_01"),
            norm_event(marker=theirs, tenant="t_ntro_core", source="src_lnx_core_07"),
        ],
    )

    # Both reach Wazuh…
    assert sink_lines(WAZUH_SINK, mine), "the Maha Power event did not reach Wazuh"
    assert sink_lines(WAZUH_SINK, theirs), "the NTRO event did not reach Wazuh"

    # …but only one reaches the partner, and not in the clear.
    partner = sink_lines(PARTNER_SINK, "h_", timeout=60)
    assert partner, "nothing reached the partner sink"
    assert not sink_lines(PARTNER_SINK, mine, timeout=5), "the user must not appear in the clear"
    assert not sink_lines(PARTNER_SINK, theirs, timeout=5), "another tenant's events must be absent"

    latest = partner[-1]
    assert latest["user"]["name"].startswith("h_")
    assert latest["src_endpoint"]["ip"].startswith("h_")
    assert latest["raw_data"] == "[REDACTED]"
    assert latest["veyra"]["tenant"] == "t_maha_power"


def test_a_filtered_event_still_leaves_a_receipt(cfg: Settings) -> None:
    """ "Why is this not in the partner feed?" is answerable from `receipts` alone."""
    marker = f"filt{uuid.uuid4().hex[:8]}"
    event = norm_event(marker=marker, tenant="t_ntro_core")
    publish(cfg, [event])
    assert sink_lines(WAZUH_SINK, marker), "the event itself must still be delivered"

    receipts = receipts_for(cfg, {event["ulpf"]["event_uid"]})
    statuses = {(receipt.route_id, receipt.status) for receipt in receipts}
    assert ("partner_masked", "filtered") in statuses, statuses


def test_a_corrected_event_carries_its_revision_to_the_sink(cfg: Settings) -> None:
    """What rule 100130 keys on: the router passes revision >= 2 through rather than hiding it."""
    marker = f"rev{uuid.uuid4().hex[:8]}"
    publish(cfg, [norm_event(marker=marker, revision=2)])
    lines = sink_lines(WAZUH_SINK, marker)
    assert lines and lines[0]["veyra"]["revision"] == 2


def test_the_metrics_expose_routes_and_breakers() -> None:
    events = metric("veyra_route_events_total")
    assert any("wazuh_main" in labels for labels in events), events
    breakers = metric("veyra_route_breaker_state")
    assert breakers, "breaker state must be visible per route"
    assert all(value in (0.0, 1.0, 2.0) for value in breakers.values())


# ---------------------------------------------------------------- AC4
@pytest.mark.slow
def test_ac4_a_sink_outage_loses_nothing(cfg: Settings) -> None:
    """Wazuh stopped for two minutes: the file keeps filling, and nothing is lost.

    With the NDJSON sink the manager is a *reader*, so stopping it cannot make the router fail —
    which is the real answer to "what if the SIEM goes down": VEYRA keeps writing and Wazuh catches
    up from the file. Every event published during the outage must be there afterwards, and every
    one of them must have a `delivered` receipt.
    """
    marker = f"down{uuid.uuid4().hex[:8]}"
    subprocess.run(["docker", "stop", "veyra-wazuh-manager"], capture_output=True, timeout=180)
    try:
        events = [norm_event(marker=f"{marker}n{index}") for index in range(20)]
        publish(cfg, events)
        time.sleep(120)  # AC4's two minutes
        lines = sink_lines(WAZUH_SINK, marker, timeout=120)
    finally:
        subprocess.run(["docker", "start", "veyra-wazuh-manager"], capture_output=True, timeout=300)

    assert len(lines) == 20, f"{len(lines)} of 20 events survived the outage"
    receipts = receipts_for(cfg, {event["ulpf"]["event_uid"] for event in events}, timeout=90)
    delivered = {
        receipt.event_uid
        for receipt in receipts
        if receipt.route_id == "wazuh_main" and receipt.status == "delivered"
    }
    assert len(delivered) == 20, f"{len(delivered)} of 20 events were receipted delivered"
