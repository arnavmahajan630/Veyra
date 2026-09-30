"""A6 AC1 and AC2: VEYRA's own normalized fields make a Wazuh alert fire.

Needs `make up SERVICES="a3 a6 c1"` and, once per machine, `make wazuh-init`. This is the demo's
payoff tested end to end — router → NDJSON file → Wazuh manager → analysisd → filebeat → indexer —
so the assertions are deliberately patient: on this laptop that chain takes tens of seconds, and a
stuck filebeat is fixed by restarting the container, never by `pkill` (S0 lost a round to that).

The rules themselves have a faster test that needs none of this: `tools/wazuh_logtest.py` pipes the
samples in `wazuh/tests/` through the manager's own engine. Run that first when something here
fails — it says whether the problem is the rule or the pipeline.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

import pytest

from veyra_common.kafka import make_producer
from veyra_common.settings import Settings
from veyra_common.topics import norm_topic

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
INDEXER = "https://localhost:9200/wazuh-alerts-*/_search"
# The manager polls the file, analysisd writes alerts.json, filebeat ships it. Minutes, not seconds.
ALERT_TIMEOUT_S = 240.0


@pytest.fixture(scope="module")
def cfg() -> Settings:
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


@pytest.fixture(scope="module", autouse=True)
def require_stack() -> None:
    running = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=30
    ).stdout
    missing = [name for name in ("veyra-router", "veyra-wazuh-manager") if name not in running]
    if missing:
        pytest.skip(f'{", ".join(missing)} not running — make up SERVICES="a3 a6 c1"')


# ---------------------------------------------------------------- publishing
def norm_event(
    *,
    marker: str,
    tier: int = 1,
    revision: int = 1,
    src_ip: str = "45.12.3.9",
    tenant: str = "t_ntro_core",
    source: str = "src_lnx_core_07",
) -> dict[str, Any]:
    """An IF-NORM-EVENT whose `message` **is** the marker.

    `data.message` is a keyword field in the wazuh-alerts template, so a phrase query never matches
    part of a longer message — the marker has to be the whole value. S0 lost an afternoon to that.
    """
    event_uid = str(uuid.uuid4())
    event: dict[str, Any] = {
        "class_uid": 3002 if tier == 1 else 0,
        "category_uid": 3 if tier == 1 else 0,
        "type_uid": 300201 if tier == 1 else 99,
        "activity_id": 1 if tier == 1 else 99,
        "severity_id": 3,
        "status_id": 2,
        "time": int(time.time() * 1000),
        "message": marker,
        "raw_data": f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password ({marker})",
        "observables": [],
        "unmapped": {},
        "metadata": {"version": "1.9.0", "product": {"name": "VEYRA", "vendor_name": "NTRO"}},
        "ulpf": {
            "v": 1,
            "event_uid": event_uid,
            "tenant_id": tenant,
            "source_id": source,
            "vendor": "linux",
            "zone": "core",
            "tier": tier,
            "conformance": "match" if tier == 1 else "unknown_template",
            "contract": {"id": "linux_sshd", "version": 1} if tier == 1 else None,
            "template": {"sig": "t_abcdef123456", "id": "failed_password"},
            "revision": revision,
            "supersedes": None if revision == 1 else f"{event_uid}@{revision - 1}",
            "replay": revision > 1,
            "received_time": "2026-09-26T14:10:00.000000000Z",
            "raw_ref": {"topic": "raw.linux", "partition": 0, "offset": 1},
        },
    }
    if tier == 1:
        event["user"] = {"name": "root"}
        event["src_endpoint"] = {"ip": src_ip}
    return event


def publish(cfg: Settings, events: list[dict[str, Any]]) -> None:
    producer = make_producer(cfg=cfg)
    for event in events:
        category = "iam" if event["class_uid"] == 3002 else "uncategorized"
        producer.produce(
            norm_topic(category), key=event["ulpf"]["event_uid"], value=json.dumps(event).encode()
        )
    assert producer.flush(30) == 0, "norm events were not accepted"


# ---------------------------------------------------------------- the indexer
def search(query: dict[str, Any], *, timeout: float = ALERT_TIMEOUT_S) -> dict[str, Any]:
    """Poll the indexer until the query matches, or give up. Returns the first hit's `_source`."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    password = os.environ.get("VEYRA_WAZUH_INDEXER_PASSWORD", "admin")
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    body = json.dumps({"query": query, "size": 1, "sort": [{"@timestamp": "desc"}]}).encode()

    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        request = urllib.request.Request(
            INDEXER,
            data=body,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15, context=context) as response:
                payload = json.loads(response.read())
            hits = payload.get("hits", {}).get("hits", [])
            if hits:
                return dict(hits[0].get("_source", {}))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(5)
    raise AssertionError(f"no alert matched within {timeout:.0f}s ({last_error or 'no hits'})")


def rule_query(rule_id: str, marker: str | None = None) -> dict[str, Any]:
    filters: list[dict[str, Any]] = [{"match_phrase": {"rule.id": rule_id}}]
    if marker is not None:
        # `data.message` is a keyword field, so this is an exact-value term query.
        filters.append({"term": {"data.message": marker}})
    return {"bool": {"filter": filters}}


# ---------------------------------------------------------------- AC1
def test_ac1_six_failures_from_one_ip_raise_the_brute_force_alert(cfg: Settings) -> None:
    """AC1: six tier-1 auth failures from the same IP inside 60 s → rule 100111 in the indexer.

    The correlation is on `veyra.src_ip`, the flat field the router writes — Wazuh's `same_field`
    takes a field name, and a nested one (`src_endpoint.ip`) is the fragile case.
    """
    marker = f"ac1-{uuid.uuid4().hex[:8]}"
    attacker = "45.12.3.9"
    events = [norm_event(marker=f"{marker}-{index}", src_ip=attacker) for index in range(6)]
    publish(cfg, events)

    alert = search(rule_query("100111"))
    assert alert["rule"]["id"] == "100111"
    assert int(alert["rule"]["level"]) >= 10, "a brute force must outrank a single failure"
    assert "veyra" in alert["rule"].get("groups", []), alert["rule"].get("groups")
    assert alert["data"]["veyra"]["src_ip"] == attacker


def test_every_veyra_event_is_visible_under_the_parent_rule(cfg: Settings) -> None:
    """Rule 100100: nothing VEYRA delivers is invisible in Wazuh, whatever its tier."""
    marker = f"vis-{uuid.uuid4().hex[:8]}"
    publish(cfg, [norm_event(marker=marker, tier=4)])
    alert = search(rule_query("100121", marker))
    assert alert["data"]["veyra"]["tier"] == "4"
    assert alert["data"]["raw_data"], "the raw bytes travel with the alert (P1)"


def test_a_tier_three_event_is_informational_not_an_alert_storm(cfg: Settings) -> None:
    """A4's restraint, enforced: an extracted-but-unclassified event must not page anyone."""
    marker = f"t3-{uuid.uuid4().hex[:8]}"
    publish(cfg, [norm_event(marker=marker, tier=3)])
    alert = search(rule_query("100120", marker))
    assert int(alert["rule"]["level"]) <= 4, "tier 3 is a review queue, not an alarm"


# ---------------------------------------------------------------- AC2
def test_ac2_a_replayed_event_alerts_as_corrected(cfg: Settings) -> None:
    """AC2: a revision-2 event fires 100130, so an analyst sees a correction, not a copy."""
    marker = f"ac2-{uuid.uuid4().hex[:8]}"
    publish(cfg, [norm_event(marker=marker, revision=2)])
    alert = search(rule_query("100130", marker))
    assert alert["data"]["veyra"]["revision"] == "2"
    assert "veyra_replay" in alert["rule"].get("groups", []), alert["rule"].get("groups")


@pytest.mark.slow
def test_ac2_replayed_t3_events_still_raise_the_brute_force_alert(cfg: Settings) -> None:
    """AC2's second half: eight replayed authsrv events, now tier 1, still trip 100111.

    This is the part that decides how 100130 is ordered in the rules file. A correction must be
    visible, but a replayed attack is still an attack — so 100111 is evaluated before 100130.
    """
    marker = f"ac2b-{uuid.uuid4().hex[:8]}"
    attacker = "103.21.4.77"
    events = [
        norm_event(
            marker=f"{marker}-{index}",
            revision=2,
            src_ip=attacker,
            tenant="t_maha_power",
            source="src_authsrv_01",
        )
        for index in range(8)
    ]
    publish(cfg, events)

    alert = search(rule_query("100111"))
    assert alert["data"]["veyra"]["src_ip"] == attacker
    assert int(alert["rule"]["level"]) >= 10
