"""CP1 — First light (docs/plan/shared/S1_integration_checkpoints.md).

Run it from the `tools` container, or from the host with the published ports:

    make cp1

Seven criteria, all observed through the public paths: real syslog sockets in, Kafka,
ClickHouse, the Wazuh sink file and the services' own health endpoints out.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# `tools/` on the path, so `checkpoints._common` and `veyra_lib` import whether this is run as
# a script, as a module, or through the compose `tools` service.
_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from checkpoints._common import (  # noqa: E402
    FAIL,
    PASS,
    REPO,
    WARN,
    Row,
    clickhouse_count,
    run,
    verdict,
    wait_for,
)
from veyra_lib import collect, http, tail_consumer  # noqa: E402

from veyra_common.hashing import sha256_hex  # noqa: E402
from veyra_common.settings import settings  # noqa: E402

SENT = 10
SSHD_SOURCE = "src_lnx_core_07"
HEALTH = {
    "control-api": "http://control-api:8000/healthz",
    "ingest-gateway": "http://ingest-gateway:8088/healthz",
    "normalizer": "http://normalizer:8201/healthz",
    "router": "http://router:8202/healthz",
    "archiver": "http://archiver:8203/healthz",
    "integrity": "http://integrity:8204/healthz",
    "lineage-indexer": "http://lineage-indexer:8205/healthz",
    "drift-worker": "http://drift-worker:8206/healthz",
    "evidence-api": "http://evidence-api:8100/health",
}

Record = tuple[str, dict[str, Any]]

# One send, judged by criteria 1, 2, 3 and 6 — the S1 table says "the same 10", so the
# capture happens once and every criterion looks at those events rather than re-sending.
_CAPTURE: tuple[list[Record], list[Record]] | None = None
_SINK_BEFORE = 0


def _capture() -> tuple[list[Record], list[Record]]:
    """Send the sshd corpus once and return (raw records, norm records) for those events."""
    global _CAPTURE, _SINK_BEFORE
    if _CAPTURE is not None:
        return _CAPTURE
    sink = Path(settings.sinks_dir) / "wazuh" / "veyra.ndjson"
    _SINK_BEFORE = sink.stat().st_size if sink.is_file() else 0
    consumer = tail_consumer(("raw.", "norm."))
    try:
        _send("--file", "linux_sshd.log")
        found = collect(
            consumer,
            lambda topic, payload: _source_of(payload) == SSHD_SOURCE,
            timeout=60,
            want=SENT * 2,
        )
    finally:
        consumer.close()
    raw = [(t, p) for t, p in found if t.startswith("raw.")]
    norm = [(t, p) for t, p in found if t.startswith("norm.")]
    _CAPTURE = (raw, norm)
    return _CAPTURE


def _uid_of(payload: dict[str, Any]) -> str | None:
    """The event uid wherever it lives: top level on an envelope, under ulpf on a norm event.

    A normalized OCSF record has no top-level ``event_uid`` — reading it there matched nothing,
    which made "the same 10 events" look like zero normalized events while the index held them
    all.
    """
    direct = payload.get("event_uid")
    if isinstance(direct, str):
        return direct
    nested = (payload.get("ulpf") or {}).get("event_uid")
    return nested if isinstance(nested, str) else None


def _source_of(payload: dict[str, Any]) -> str | None:
    """`source_id` wherever it lives: top level on an envelope, under ulpf on a norm event."""
    direct = payload.get("source_id")
    if isinstance(direct, str):
        return direct
    nested = (payload.get("ulpf") or {}).get("source_id")
    return nested if isinstance(nested, str) else None


def _send(*args: str) -> None:
    subprocess.run(
        [sys.executable, str(REPO / "demo" / "tools" / "send_syslog.py"), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ---------------------------------------------------------------- 1
def c1_syslog_to_raw() -> list[Row]:
    """10 sshd lines over UDP land on raw.linux, resolved, with a correct raw_sha256."""
    found, _ = _capture()
    envelopes = [payload for _, payload in found]

    rows = [
        verdict(
            len(envelopes) >= SENT,
            "envelopes on raw.linux",
            f"{len(envelopes)} of {SENT} within 40 s",
        ),
        verdict(
            all(topic == "raw.linux" for topic, _ in found),
            "routed by vendor to raw.linux",
            ", ".join(sorted({topic for topic, _ in found})) or "nothing",
        ),
        verdict(
            all(e.get("source_id") == SSHD_SOURCE for e in envelopes) and bool(envelopes),
            "source resolved from the inventory",
            f"{SSHD_SOURCE} on {len(envelopes)} envelope(s)",
        ),
    ]

    # Recompute the hash ourselves: the point of P1 is that it is the bytes' own hash.
    mismatched = []
    for envelope in envelopes:
        raw = envelope.get("raw_b64")
        if not isinstance(raw, str):
            mismatched.append(f"{envelope['event_uid']}: no raw_b64 in the envelope")
            continue
        if sha256_hex(base64.b64decode(raw)) != envelope.get("raw_sha256"):
            mismatched.append(str(envelope["event_uid"]))
    rows.append(
        verdict(
            not mismatched and bool(envelopes),
            "raw_sha256 recomputed from the bytes",
            "every envelope" if not mismatched else f"{len(mismatched)} wrong: {mismatched[:3]}",
        )
    )
    return rows


# ---------------------------------------------------------------- 2
def c2_normalized_tier1() -> list[Row]:
    """The same events reach norm.iam as tier 1, class 3002, under linux_sshd."""
    raw, found = _capture()
    uids = {_uid_of(p) for _, p in raw}
    events = [payload for _, payload in found if _uid_of(payload) in uids]
    tier1 = [e for e in events if (e.get("ulpf") or {}).get("tier") == 1]
    iam = [topic for topic, _ in found if topic == "norm.iam"]
    contracts = {((e.get("ulpf") or {}).get("contract") or {}).get("id") for e in tier1}
    return [
        verdict(bool(events), "events on norm.*", f"{len(events)} within 40 s"),
        verdict(bool(iam), "auth events on norm.iam", f"{len(iam)} of {len(events)}"),
        verdict(bool(tier1), "tier 1", f"{len(tier1)} of {len(events)}"),
        verdict(
            any(e.get("class_uid") == 3002 for e in tier1),
            "class_uid 3002 (authentication)",
            str(sorted(str(e.get("class_uid")) for e in tier1)),
        ),
        verdict(
            "linux_sshd" in contracts,
            "ulpf.contract.id linux_sshd",
            str(sorted(c for c in contracts if c)),
        ),
    ]


# ---------------------------------------------------------------- 3
def c3_reaches_wazuh_sink() -> list[Row]:
    """The router's Wazuh sink has one NDJSON line per event. Discover is the human half."""
    sink = Path(settings.sinks_dir) / "wazuh" / "veyra.ndjson"
    if not sink.is_file():
        return [Row(FAIL, "wazuh sink file", f"{sink} does not exist")]
    _capture()
    before = _SINK_BEFORE

    def grew() -> bool:
        return sink.stat().st_size > before

    rows = [
        verdict(wait_for(grew, timeout_s=40), "sink grew after new events", f"was {before} bytes")
    ]
    tail = sink.read_text(encoding="utf-8", errors="replace").splitlines()[-SENT:]
    parsed = []
    for line in tail:
        with contextlib.suppress(ValueError):
            parsed.append(json.loads(line))
    rows.append(
        verdict(
            len(parsed) == len(tail) and bool(tail),
            "one JSON object per line",
            f"{len(parsed)} of {len(tail)} parsed",
        )
    )
    rows.append(Row(WARN, "rule 100100 in Wazuh Discover", "human: dashboard, rule.groups veyra"))
    return rows


# ---------------------------------------------------------------- 4
def c4_garbage_and_health() -> list[Row]:
    """Garbage from an unregistered sender is tier 4, and nothing fell over."""
    consumer = tail_consumer(("raw.", "norm."))
    try:
        _send("--file", "garbage.bin")
        found = collect(consumer, lambda topic, payload: True, timeout=40, want=200)
    finally:
        consumer.close()
    unregistered = [t for t, _ in found if t == "raw.unregistered"]
    tiers = {(p.get("ulpf") or {}).get("tier") for t, p in found if t.startswith("norm.")}
    rows = [
        verdict(
            bool(unregistered), "garbage on raw.unregistered", f"{len(unregistered)} record(s)"
        ),
        verdict(
            4 in tiers,
            "delivered at tier 4, not dropped",
            f"tiers seen: {sorted(str(t) for t in tiers)}",
        ),
    ]
    client = http(timeout=10)
    for name, url in HEALTH.items():
        try:
            code = client.get(url).status_code
            rows.append(Row(PASS if code < 400 else FAIL, f"{name} healthy", f"HTTP {code}"))
        except Exception as exc:
            rows.append(Row(FAIL, f"{name} healthy", f"{type(exc).__name__}: {exc}"))
    return rows


# ---------------------------------------------------------------- 5
def c5_segment_sealed() -> list[Row]:
    """A segment seals inside VEYRA_SEGMENT_MAX_SECONDS, read-only, and is indexed."""
    vault = Path(settings.vault_dir)
    budget = float(settings.segment_max_seconds) + 20
    _send("--file", "linux_sshd.log")

    def sealed() -> bool:
        return any(vault.glob("*/*/*.seg"))

    rows = [
        verdict(
            wait_for(sealed, timeout_s=budget, interval_s=2),
            "a segment sealed",
            f"within {budget:.0f}s of VEYRA_SEGMENT_MAX_SECONDS={settings.segment_max_seconds}",
        )
    ]
    segments = sorted(vault.glob("*/*/*.seg"))
    if segments:
        mode = stat.S_IMODE(os.stat(segments[-1]).st_mode)
        rows.append(
            verdict(mode == 0o444, "sealed file is read-only", f"{segments[-1].name} mode {mode:o}")
        )
    # The archiver publishes IF-VAULT-INDEX at each seal; the indexer turns it into these rows.
    url = settings.clickhouse_url
    db = settings.clickhouse_db
    count = clickhouse_count(f"SELECT count() FROM {db}.segments", url)
    rows.append(
        verdict(
            count is not None and count > 0,
            "vault_index sealed record indexed",
            "ClickHouse unreachable" if count is None else f"{count} row(s) in {db}.segments",
        )
    )
    return rows


# ---------------------------------------------------------------- 6
def c6_clickhouse_counts() -> list[Row]:
    """The lineage index holds the events we sent."""
    url, db = settings.clickhouse_url, settings.clickhouse_db
    raw, _ = _capture()
    uids = [uid for _, p in raw if (uid := _uid_of(p))]
    rows: list[Row] = []
    if not uids:
        return [Row(FAIL, "indexed counts", "no event uids were captured")]
    quoted = ",".join(f"'{uid}'" for uid in uids)
    for table in ("raw_events", "norm_lineage"):
        query = f"SELECT count(DISTINCT event_uid) FROM {db}.{table} WHERE event_uid IN ({quoted})"
        got = clickhouse_count(query, url)
        rows.append(
            verdict(
                got == len(uids),
                f"{table} has every sent event",
                "ClickHouse unreachable" if got is None else f"{got} of {len(uids)}",
            )
        )
    return rows


# ---------------------------------------------------------------- 7
def c7_restart_no_duplicates() -> list[Row]:
    """Killing the normalizer mid-stream loses nothing and duplicates nothing."""
    url, db = settings.clickhouse_url, settings.clickhouse_db
    before = clickhouse_count(f"SELECT count() FROM {db}.norm_lineage", url)
    if before is None:
        return [Row(FAIL, "normalizer restart", "ClickHouse unreachable")]

    killed, how = _kill_and_restart_normalizer()
    if not killed:
        # The invariant below is checkable from anywhere; only the kill needs Docker. Say what
        # to run rather than failing a criterion for a missing CLI inside the container.
        return [
            Row(
                WARN,
                "normalizer killed mid-stream",
                f"{how}; run `docker kill --signal=KILL veyra-normalizer && "
                "docker start veyra-normalizer` from the host, then `make cp1 ARGS='--only 7'`",
            ),
            _no_duplicate_revisions(url, db),
        ]
    _send("--file", "linux_sshd.log")
    time.sleep(5)

    def caught_up() -> bool:
        now = clickhouse_count(f"SELECT count() FROM {db}.norm_lineage", url)
        return now is not None and now > before

    return [
        verdict(
            wait_for(caught_up, timeout_s=120, interval_s=3),
            "events caught up after the restart",
            how,
        ),
        _no_duplicate_revisions(url, db),
    ]


def _no_duplicate_revisions(url: str, db: str) -> Row:
    """Exactly-once is the point: a restart may redo work, never record it twice."""
    duplicates = clickhouse_count(
        "SELECT count() FROM (SELECT event_uid, revision, count() AS n "
        f"FROM {db}.norm_lineage GROUP BY event_uid, revision HAVING n > 1)",
        url,
    )
    return verdict(
        duplicates == 0,
        "no duplicate (event_uid, revision)",
        "ClickHouse unreachable" if duplicates is None else f"{duplicates} duplicated key(s)",
    )


def _kill_and_restart_normalizer() -> tuple[bool, str]:
    """SIGKILL the normalizer and start it again, over the Docker socket or the CLI.

    The socket is the path that works from inside the `tools` container, which has no Docker
    CLI; the CLI is the path that works when a checkpoint is run from the host.
    """
    import httpx

    socket_path = "/var/run/docker.sock"
    if Path(socket_path).exists():
        try:
            transport = httpx.HTTPTransport(uds=socket_path)
            with httpx.Client(transport=transport, base_url="http://docker", timeout=60.0) as api:
                api.post("/v1.44/containers/veyra-normalizer/kill", params={"signal": "KILL"})
                started = api.post("/v1.44/containers/veyra-normalizer/start")
            if started.status_code < 400 or started.status_code == 304:
                return True, "over the Docker socket"
            return False, f"the Docker socket answered {started.status_code}"
        except Exception as exc:
            return False, f"the Docker socket is mounted but unusable ({type(exc).__name__})"

    code, out = subprocess.getstatusoutput("docker kill --signal=KILL veyra-normalizer")
    if code != 0:
        return False, out.strip().splitlines()[-1][:80] if out.strip() else "docker kill failed"
    subprocess.getstatusoutput("docker start veyra-normalizer")
    return True, "with the docker CLI"


CHECKS = {
    "1": ("10 syslog lines land on raw.linux, stamped", c1_syslog_to_raw),
    "2": ("the same events reach norm.iam at tier 1", c2_normalized_tier1),
    "3": ("they reach the Wazuh sink, one JSON per line", c3_reaches_wazuh_sink),
    "4": ("garbage is tier 4 and nothing crashed", c4_garbage_and_health),
    "5": ("a segment seals, read-only, and is indexed", c5_segment_sealed),
    "6": ("ClickHouse counts match what was sent", c6_clickhouse_counts),
    "7": ("a normalizer restart duplicates nothing", c7_restart_no_duplicates),
}

if __name__ == "__main__":
    raise SystemExit(run("CP1", CHECKS))
