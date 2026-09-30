"""Smoke check after ./veyra.sh up: is the stack answering, and does a log get through?

Runs inside the compose ``tools`` container (services by compose name). PASS/WARN/FAIL per
line, exit 1 on any FAIL. Checks only what is built; a service that was not started (for
example Wazuh on the laptop profile) is a WARN, not a FAIL.

    python tools/veyra_check.py            # everything
    python tools/veyra_check.py --no-flow  # skip the end-to-end probe
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from veyra_lib import Results, bold, collect, http, tail_consumer

from veyra_common.kafka import make_consumer
from veyra_common.settings import settings
from veyra_common.topics import topic_specs

# name, url, required
HEALTH = [
    ("control-api", "http://control-api:8000/healthz", True),
    ("ingest-gateway", "http://ingest-gateway:8088/healthz", True),
    ("normalizer", "http://normalizer:8201/healthz", True),
    ("drift-worker", "http://drift-worker:8206/healthz", True),
    ("lineage-indexer", "http://lineage-indexer:8205/healthz", True),
    ("archiver", "http://archiver:8203/healthz", True),
    ("integrity", "http://integrity:8204/healthz", True),
    ("evidence-api", "http://evidence-api:8100/health", True),
    ("console (via Caddy)", "http://caddy:8080/", True),
    ("control-api (via Caddy)", "http://caddy:8080/api/control/healthz", True),
    ("evidence-api (via Caddy)", "http://caddy:8080/api/evidence/roots", True),
    ("ollama", "http://ollama:11434/api/tags", False),
]


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


def check_flow(results: Results) -> None:
    import socket

    print(bold("\n  End to end: syslog -> edge -> Kafka -> normalizer"))
    user = f"probe{uuid.uuid4().hex[:8]}"
    line = (
        f"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user {user} "
        f"from 45.12.3.9 port 52144 ssh2"
    ).encode()
    consumer = tail_consumer(("raw.", "norm."))
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
        check_flow(results)
    return results.summary()


if __name__ == "__main__":
    raise SystemExit(main())
