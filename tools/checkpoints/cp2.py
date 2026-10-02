"""CP2 — Messy + evidence (docs/plan/shared/S1_integration_checkpoints.md).

    make cp2

Six criteria. The messy multi-line auth events go in over real syslog TCP; what comes out is
judged on the wire, in the index, and through the evidence API.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# `tools/` on the path, so `checkpoints._common` and `veyra_lib` import whether this is run as
# a script, as a module, or through the compose `tools` service.
_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from checkpoints._common import FAIL, REPO, WARN, Row, run, verdict  # noqa: E402
from veyra_lib import collect, http, tail_consumer  # noqa: E402

from veyra_common.settings import settings  # noqa: E402

AUTHSRV = "src_authsrv_01"
EVIDENCE = "http://evidence-api:8100"
VERIFY_BUDGET_S = 2.0
_NORM: list[dict[str, Any]] | None = None
_RAW: list[dict[str, Any]] = []
_SEALED_UID: str | None = None


def _send_t3() -> list[dict[str, Any]]:
    """The T3 failed-login corpus over syslog TCP, with its stack-trace continuation lines."""
    global _NORM, _RAW
    if _NORM is not None:
        return _NORM
    consumer = tail_consumer(("raw.", "norm."))
    try:
        subprocess.run(
            [
                sys.executable,
                str(REPO / "demo" / "tools" / "send_syslog.py"),
                "--file",
                "authsrv_t3_failed.log",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        found = collect(consumer, lambda topic, payload: True, timeout=60, want=200)
    finally:
        consumer.close()
    _NORM = [p for t, p in found if t.startswith("norm.")]
    _RAW = [p for t, p in found if t.startswith("raw.")]
    return _NORM


# ---------------------------------------------------------------- 1
def c1_multiline_tier3() -> list[Row]:
    """One envelope per logical event, tier 3, both IPs and the user as observables."""
    norm = _send_t3()
    raw = _RAW
    tier3 = [e for e in norm if (e.get("ulpf") or {}).get("tier") == 3]
    multiline = [e for e in raw if ((e.get("framing") or {}).get("method")) == "multiline_join"]
    observables = {
        o.get("value") for e in tier3 for o in (e.get("observables") or []) if o.get("value")
    }
    hints = {(e.get("ulpf") or {}).get("class_hint") for e in tier3}
    return [
        verdict(bool(raw), "envelopes stamped", f"{len(raw)} on raw.*"),
        verdict(
            bool(multiline),
            "multi-line events joined into one envelope",
            f"{len(multiline)} with framing multiline_join",
        ),
        verdict(bool(tier3), "tier 3", f"{len(tier3)} of {len(norm)} normalized"),
        verdict(
            {"103.21.4.77", "10.2.3.4"} <= observables,
            "both IPs pulled out as observables",
            str(sorted(observables)[:6]),
        ),
        verdict(
            any("sharma" in str(v) for v in observables),
            "the user pulled out as an observable",
            str(sorted(v for v in observables if "sharma" in str(v))),
        ),
        verdict(
            3002 in hints,
            "class_hint 3002, as a hint only",
            f"hints: {sorted(h for h in hints if h)}",
        ),
    ]


# ---------------------------------------------------------------- 2
def c2_offsets_slice_the_bytes() -> list[Row]:
    """P4: every field offset must slice its own value out of the raw bytes."""
    import base64

    norm = _send_t3()
    raw_by_uid = {
        str(e["event_uid"]): base64.b64decode(e["raw_b64"]) for e in _RAW if e.get("raw_b64")
    }
    checked = wrong = 0
    examples: list[str] = []
    for event in norm:
        ulpf = event.get("ulpf") or {}
        raw = raw_by_uid.get(str(ulpf.get("event_uid") or event.get("event_uid")))
        if raw is None:
            continue
        for path, span in (ulpf.get("field_offsets") or {}).items():
            if not isinstance(span, (list, tuple)) or len(span) != 2:
                continue
            checked += 1
            start, end = int(span[0]), int(span[1])
            sliced = raw[start:end].decode("utf-8", errors="replace")
            value = _at(event, path)
            if value is not None and str(value) != sliced:
                wrong += 1
                if len(examples) < 3:
                    examples.append(f"{path}: {sliced!r} != {value!r}")
    return [
        verdict(checked > 0, "field offsets present", f"{checked} offset(s) checked"),
        verdict(
            wrong == 0 and checked > 0,
            "raw[start:end] equals the mapped value",
            "100%" if wrong == 0 else f"{wrong} of {checked} wrong: {examples}",
        ),
    ]


def _at(event: dict[str, Any], path: str) -> object | None:
    node: object = event
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# ---------------------------------------------------------------- 3
def c3_verify_a_sealed_event() -> list[Row]:
    """`GET /evidence/{uid}/verify` answers ok, with all eight steps, inside 2 s."""
    client = http(timeout=30)
    uid = _a_sealed_event(client)
    if uid is None:
        return [Row(FAIL, "a sealed event to verify", "none sealed and signed within 150 s")]
    started = time.monotonic()
    report = client.get(f"{EVIDENCE}/evidence/{uid}/verify").json()
    elapsed = time.monotonic() - started
    steps = report.get("steps") or []
    declared = [s for s in steps if s.get("status")]
    return [
        verdict(bool(report.get("verified")), "verify says ok", f"{uid}"),
        verdict(len(steps) == 8, "eight steps reported", f"{len(steps)} steps"),
        Row(
            WARN if declared else "PASS",
            "every step implemented",
            f"declared not implemented: {[s['id'] for s in declared]}" if declared else "all eight",
        ),
        verdict(elapsed < VERIFY_BUDGET_S, "verify under 2 s", f"{elapsed * 1000:.0f} ms"),
    ]


def _a_sealed_event(client: httpx.Client) -> str | None:
    """The newest event that verifies. Sealing plus the Merkle window takes up to ~90 s."""
    global _SEALED_UID
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        hits = (
            client.get(f"{EVIDENCE}/lineage/search", params={"q": "sharma", "limit": 20})
            .json()
            .get("hits")
            or []
        )
        for hit in hits:
            uid = str(hit["event_uid"])
            if client.get(f"{EVIDENCE}/evidence/{uid}/verify").json().get("verified"):
                _SEALED_UID = uid
                return uid
        time.sleep(3)
    return None


# ---------------------------------------------------------------- 4
def c4_signed_roots_and_chain() -> list[Row]:
    """A signed root per window, the ledger chained, and the immudb anchor (declared)."""
    roots = http(timeout=30).get(f"{EVIDENCE}/evidence/roots", params={"limit": 50}).json()
    entries = roots.get("roots") or []
    audit = roots.get("audit_status")
    anchored = [r for r in entries if (r.get("payload") or {}).get("immudb_verified")]
    return [
        verdict(bool(entries), "a signed root exists", f"{roots.get('count')} in the ledger"),
        verdict(
            audit == "PASS",
            "the ledger chain links (prev_signed_sha256)",
            f"ledger audit: {audit}",
        ),
        verdict(
            all(r.get("signature_ok") is not False for r in entries),
            "every root's signature verifies",
            f"{sum(1 for r in entries if r.get('signature_ok'))} of {len(entries)} checked ok",
        ),
        Row(
            WARN if not anchored else "PASS",
            "immudb verified-get",
            "not implemented in this build (B3, declared): verify step 8 reports it",
        ),
    ]


# ---------------------------------------------------------------- 5
def c5_console_live() -> list[Row]:
    """The Overview's numbers come from the index; the human half is that they move."""
    client = http(timeout=15)
    overview = client.get("http://caddy:8080/api/lineage/overview").json()
    sources = client.get("http://caddy:8080/api/lineage/sources").json()
    tiers = overview.get("totals_by_tier") or {}
    return [
        verdict("as_of" in overview, "overview carries as_of", str(overview.get("as_of"))),
        verdict(
            set(tiers) == {"1", "2", "3", "4"},
            "tiers keyed as the console reads them",
            str(sorted(tiers)),
        ),
        verdict(
            any(t for t in tiers.values()),
            "live tier counts, not zeros",
            str(tiers),
        ),
        verdict(
            bool(sources) and all("zone" in s and "tiers" in s for s in sources),
            "source health carries zone and tier mix",
            f"{len(sources)} source(s)",
        ),
        Row(WARN, "Overview and Sources update within 2 s", "human: watch the console"),
    ]


# ---------------------------------------------------------------- 6
def c6_contracts_compile() -> list[Row]:
    """Every seeded contract compiles and its goldens pass."""
    proc = subprocess.run(
        [sys.executable, "-m", "veyra_contracts.check", str(settings.contracts_repo)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    detail = tail[-1] if tail else ""
    return [verdict(proc.returncode == 0, "contracts compile and goldens pass", detail)]


CHECKS = {
    "1": ("the messy multi-line auth events are tier 3 with observables", c1_multiline_tier3),
    "2": ("every field offset slices its own value", c2_offsets_slice_the_bytes),
    "3": ("verify answers ok in under 2 s", c3_verify_a_sealed_event),
    "4": ("signed roots exist and the ledger chains", c4_signed_roots_and_chain),
    "5": ("the console's live panels read the index", c5_console_live),
    "6": ("the contract compiler and goldens pass", c6_contracts_compile),
}

if __name__ == "__main__":
    raise SystemExit(run("CP2", CHECKS))
