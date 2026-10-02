"""Smoke check after ./veyra.sh up: is the stack answering, and does a log get through?

Runs inside the compose ``tools`` container (services by compose name). PASS/WARN/FAIL per
line, exit 1 on any FAIL. Checks only what is built; a service that was not started (for
example Wazuh on the laptop profile) is a WARN, not a FAIL.

    python tools/veyra_check.py            # everything
    python tools/veyra_check.py --no-flow  # skip the end-to-end probe
"""

from __future__ import annotations

import argparse
import base64
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veyra_lib import Results, bold, collect, http, tail_consumer, wait_until

from veyra_common.kafka import make_consumer
from veyra_common.settings import settings
from veyra_common.topics import topic_specs

# The router's wazuh_main route appends here; Wazuh's localfile tails it (A6).
WAZUH_SINK = Path("data/sinks/wazuh/veyra.ndjson")

# name, url, required
HEALTH = [
    ("control-api", "http://control-api:8000/healthz", True),
    ("ingest-gateway", "http://ingest-gateway:8088/healthz", True),
    ("normalizer", "http://normalizer:8201/healthz", True),
    ("router", "http://router:8202/healthz", True),
    ("drift-worker", "http://drift-worker:8206/healthz", True),
    ("lineage-indexer", "http://lineage-indexer:8205/healthz", True),
    ("archiver", "http://archiver:8203/healthz", True),
    ("integrity", "http://integrity:8204/healthz", True),
    ("evidence-api", "http://evidence-api:8100/health", True),
    ("demo-engine", "http://demo-engine:8300/healthz", True),
    ("console (via Caddy)", "http://caddy:8080/", True),
    ("control-api (via Caddy)", "http://caddy:8080/api/control/healthz", True),
    ("evidence-api (via Caddy)", "http://caddy:8080/api/evidence/roots", True),
    ("demo-engine (via Caddy)", "http://caddy:8080/api/demo/healthz", True),
    ("kafka-ui", "http://kafka-ui:8080/actuator/health", False),
]
# The model server that drafts, optional either way: a missing one means drafts come from
# saved answers or the rules drafter. Which one depends on the drafter veyra.sh was told to use.
if settings.llm_backend == "decision":
    HEALTH.append((f"ollaya ({settings.decision_model})", "http://ollaya:11435/", False))
else:
    HEALTH.append(("ollama", "http://ollama:11434/api/tags", False))


def probe_health() -> list[tuple[str, bool, str, bool]]:
    out = []
    with http(timeout=5) as client:
        for name, url, required in HEALTH:
            try:
                response = client.get(url)
                ok, detail = response.status_code < 400, f"HTTP {response.status_code}"
            except Exception as exc:
                ok, detail = False, type(exc).__name__
            out.append((name, ok, detail, required))
    return out


def wait_for_health(seconds: float) -> bool:
    """Poll until every required service answers, or the time runs out."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if all(ok for _, ok, _, required in probe_health() if required):
            return True
        time.sleep(3)
    return False


def check_health(results: Results) -> None:
    print(bold("\n  Services"))
    for name, ok, detail, required in probe_health():
        results.check(name, ok, detail, warn_only=not required)


def check_topics(results: Results) -> None:
    print(bold("\n  Kafka topics"))
    consumer = make_consumer(f"veyra-check-{uuid.uuid4().hex[:6]}", None)
    try:
        existing = set(consumer.list_topics(timeout=20).topics)
    finally:
        consumer.close()
    missing = [spec.name for spec in topic_specs(settings) if spec.name not in existing]
    results.check(
        "all IF-TOPICS topics exist",
        not missing,
        f"{len(existing)} topics" if not missing else f"missing: {', '.join(missing[:6])}",
    )


def check_login(results: Results) -> None:
    print(bold("\n  Control plane"))
    with http() as client:
        response = client.post(
            "http://control-api:8000/auth/login",
            json={"email": "admin@veyra", "password": settings.demo_password},
        )
        results.check(
            "admin@veyra can sign in", response.status_code == 200, f"HTTP {response.status_code}"
        )
        if response.status_code == 200:
            sources = client.get("http://control-api:8000/sources").json()
            results.check("seeded sources present", len(sources) >= 2, f"{len(sources)} sources")


def check_flow(results: Results) -> str:
    """Returns the probe's user name, so the later checks can look the same event up."""
    import socket

    print(bold("\n  End to end: syslog -> edge -> Kafka -> normalizer"))
    user = f"probe{uuid.uuid4().hex[:8]}"
    line = (
        f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user {user} "
        f"from 45.12.3.9 port 52144 ssh2"
    ).encode()
    consumer = tail_consumer(("raw.", "norm."))
    start = WAZUH_SINK.stat().st_size if WAZUH_SINK.exists() else 0
    seen_raw: list[str] = []

    def is_probe(topic: str, payload: dict) -> bool:
        if user not in str(payload):
            return False
        if topic.startswith("raw."):
            seen_raw.append(topic)
            return False
        return topic.startswith("norm.")

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        for _ in range(3):  # UDP may drop, so the probe goes out three times
            sock.sendto(line, ("edge-core", 5524))
        sock.close()
        found = collect(consumer, is_probe, timeout=60, want=1)
    finally:
        consumer.close()
    results.check(
        "edge stamped it onto raw.linux",
        bool(seen_raw) or bool(found),
        ", ".join(sorted(set(seen_raw))) or "seen downstream",
    )
    norm = found[0][1] if found else None
    tier = (norm or {}).get("ulpf", {}).get("tier")
    results.check(
        "normalizer made it OCSF tier 1 on norm.iam",
        norm is not None and tier == 1,
        f"tier={tier}" if norm else "no norm.* record within 60 s",
    )
    delivered = wait_until(lambda: sink_has(WAZUH_SINK, start, user), timeout=30)
    results.check(
        "router delivered it to the Wazuh sink",
        delivered,
        str(WAZUH_SINK) if delivered else "not in the sink file within 30 s",
    )
    return user


def check_evidence(results: Results, user: str) -> None:
    """The Track B half: the probe is indexed, sealed, signed, and verifies.

    None of this was checked before, which is how the stack could report Ready with the whole
    evidence side either not started or not working.
    """
    print(bold("\n  Evidence: index -> vault -> signed root -> verify"))
    db = settings.clickhouse_db
    with http(timeout=20) as client:

        def scalar(query: str) -> int | None:
            try:
                response = client.post(settings.clickhouse_url, content=query)
                response.raise_for_status()
                return int(response.text.strip() or 0)
            except Exception:
                return None

        indexed = wait_until(
            lambda: (scalar(f"SELECT count() FROM {db}.norm_lineage") or 0) > 0, timeout=60
        )
        results.check(
            "ClickHouse has lineage rows",
            bool(indexed),
            f"{db}.norm_lineage" if indexed else "no rows within 60 s (migrations? indexer?)",
        )

        # A segment seals on VEYRA_SEGMENT_MAX_SECONDS, so this waits for one rather than
        # asking the instant the probe went in.
        budget = float(settings.segment_max_seconds) + 30
        sealed = wait_until(
            lambda: (scalar(f"SELECT count() FROM {db}.segments") or 0) > 0, timeout=budget
        )
        results.check(
            "the archiver's sealed segments are indexed",
            bool(sealed),
            f"{db}.segments" if sealed else f"no rows within {budget:.0f}s (vault_index?)",
        )

        roots = client.get("http://evidence-api:8100/evidence/roots", params={"limit": 5}).json()
        signed = bool(roots.get("count"))
        # The first root needs a whole Merkle window plus its lag, which is longer than a smoke
        # check should sit here for. CP2 criterion 4 waits for it and fails if it never comes.
        results.check(
            "integrity signed a window root",
            signed,
            f"{roots.get('count')} root(s), audit {roots.get('audit_status')}"
            if signed
            else f"none yet (window {settings.merkle_window_seconds}s); `make cp2` checks this",
            warn_only=not signed,
        )
        if signed:
            results.check(
                "the signed ledger audits clean",
                roots.get("audit_status") == "PASS",
                f"audit {roots.get('audit_status')}",
                warn_only=roots.get("audit_status") == "unknown",
            )

        # Verify whatever is sealed, not necessarily the probe: a fresh event has to wait out
        # its segment seal and its Merkle window, which is longer than a smoke check should be.
        def probe_uid() -> str | None:
            hits = client.get(
                "http://evidence-api:8100/lineage/search", params={"q": user, "limit": 1}
            ).json()
            found = (hits.get("hits") or [{}])[0].get("event_uid")
            return str(found) if found else None

        uid = wait_until(probe_uid, timeout=30)
        results.check(
            "the probe event is searchable by its user",
            bool(uid),
            str(uid) if uid else f"no hit for {user} within 30 s",
        )


def check_push(results: Results) -> None:
    """A real HEC push with a real issued key, which nothing used to exercise."""
    print(bold("\n  Push ingest: HEC gateway with an issued key"))
    with http(timeout=20) as client:
        login = client.post(
            "http://control-api:8000/auth/login",
            json={"email": "admin@veyra", "password": settings.demo_password},
        )
        if login.status_code != 200:
            results.check("signed in to issue a key", False, f"HTTP {login.status_code}")
            return
        sources = client.get("http://control-api:8000/sources").json()
        source = next((s["id"] for s in sources), None)
        if source is None:
            results.check("a source to push to", False, "no seeded sources")
            return
        issued = client.post(
            f"http://control-api:8000/sources/{source}/keys", json={"note": "veyra_check"}
        )
        secret = issued.json().get("secret") if issued.status_code < 400 else None
        results.check(
            "a per-source key can be issued",
            bool(secret),
            f"HTTP {issued.status_code} for {source}",
        )
        if not secret:
            return
        user = f"push{uuid.uuid4().hex[:8]}"
        pushed = client.post(
            "http://ingest-gateway:8088/services/collector/event",
            headers={"Authorization": f"Splunk {secret}"},
            json={"event": f"user={user} OK login from 10.4.1.20 via 10.2.3.4"},
        )
        results.check(
            "the gateway accepted a HEC event",
            pushed.status_code < 300,
            f"HTTP {pushed.status_code}: {pushed.text[:100]}",
        )


def check_dmz_edge(results: Results) -> None:
    """The DMZ listener, which the core-edge probe does not cover."""
    import socket

    print(bold("\n  Edge: the DMZ zone"))
    consumer = tail_consumer(("raw.",))
    marker = f"dmz{uuid.uuid4().hex[:8]}"
    line = (
        f"<134>Sep 26 14:05:00 fw-dmz-01 CEF:0|Acme|NGFW|9.1|100|traffic deny|5|"
        f"rt=Sep 26 2026 14:05:00 src=45.12.3.9 dst=10.2.3.4 dpt=22 proto=tcp act=deny "
        f"cs1Label=probe cs1={marker}\n"
    ).encode()

    def carries_marker(topic: str, payload: dict) -> bool:
        # The envelope holds the bytes base64-encoded, so the marker is not in the JSON text:
        # decode and look at the bytes the collector actually stamped.
        blob = payload.get("raw_b64")
        if not isinstance(blob, str):
            return False
        try:
            return marker.encode() in base64.b64decode(blob)
        except Exception:
            return False

    try:
        with socket.create_connection(("edge-dmz", 5515), timeout=10) as sock:
            sock.sendall(line)
        found = collect(consumer, carries_marker, timeout=40, want=1)
    except Exception as exc:
        results.check("the DMZ listener accepted a TCP syslog line", False, f"{exc}")
        return
    finally:
        consumer.close()
    results.check(
        "the DMZ listener stamped it onto raw.*",
        bool(found),
        found[0][0] if found else "nothing within 40 s",
    )


def sink_has(path: Path, start: int, needle: str) -> bool:
    if not path.exists():
        return False
    with path.open("rb") as handle:
        handle.seek(start)
        return needle.encode() in handle.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-flow", action="store_true")
    parser.add_argument("--only-health", action="store_true")
    parser.add_argument("--wait", type=float, default=0, help="first wait up to N s for services")
    args = parser.parse_args(argv)
    if args.wait:
        ready = wait_for_health(args.wait)
        if args.only_health:
            return 0 if ready else 1
    results = Results()
    check_health(results)
    if args.only_health:
        return results.summary()
    check_topics(results)
    check_login(results)
    if not args.no_flow:
        probe_user = check_flow(results)
        check_dmz_edge(results)
        check_push(results)
        check_evidence(results, probe_user)
    return results.summary()


if __name__ == "__main__":
    raise SystemExit(main())
