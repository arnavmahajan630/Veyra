"""Golden tests: the whole demo corpus through the real contracts, snapshot-compared.

This is the phase's strongest guarantee. For every line of every corpus file we record tier,
class, activity, the mapped OCSF paths, and — critically — **the bytes each offset points at**.
A regression in peeling, template matching, mapping or offsets changes a snapshot, so it cannot
pass unnoticed.

Snapshots live in `tests/expected/*.json` and are regenerated deliberately:

    uv run python packages/veyra_engine/tests/test_golden.py --update

`received_time` is fixed, so results are stable: the engine reads no clock (P3), which is what
makes snapshotting honest in the first place.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from veyra_common.envelope import stamp
from veyra_common.models import Envelope, NormEvent
from veyra_engine import Engine, EngineContext, mini_compile, provenance_check

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
EXPECTED = Path(__file__).resolve().parent / "expected"
TEST_CONTRACTS = Path(__file__).resolve().parent / "contracts"
SEED_CONTRACTS = REPO / "contracts-repo" / "t_ntro_core"

# A fixed arrival time, so year inference and clock skew are deterministic.
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# corpus file -> (contract path, source_id, tenant, vendor, zone, transport, framing)
CASES: dict[str, tuple[Path, str, str, str, str, str, str]] = {
    "linux_sshd.log": (
        SEED_CONTRACTS / "linux_sshd.yaml",
        "src_lnx_core_07",
        "t_ntro_core",
        "linux",
        "core",
        "syslog_udp",
        "datagram",
    ),
    "acme_ngfw_cef.log": (
        SEED_CONTRACTS / "acme_ngfw_cef.yaml",
        "src_fw_dmz_01",
        "t_ntro_core",
        "acme_ngfw",
        "dmz",
        "syslog_tcp",
        "newline",
    ),
    "authsrv_t1_ok.log": (
        TEST_CONTRACTS / "authsrv.yaml",
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "dmz",
        "http_hec_event",
        "http_body",
    ),
    "authsrv_t2_session.log": (
        TEST_CONTRACTS / "authsrv.yaml",
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "dmz",
        "http_hec_event",
        "http_body",
    ),
}


def make_envelope(raw: bytes, case: tuple[Any, ...], index: int) -> Envelope:
    _, source_id, tenant, vendor, zone, transport, framing = case
    return stamp(
        raw,
        collector_id="golden",
        transport=transport,  # type: ignore[arg-type]
        framing_method=framing,  # type: ignore[arg-type]
        zone=zone,  # type: ignore[arg-type]
        tenant_id=tenant,
        source_id=source_id,
        vendor=vendor,
        # Fixed uid and time: the snapshot must not move between runs.
        event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
        received_time=RECEIVED,
    )


def engine_for(contract_path: Path) -> Engine:
    engine = Engine(EngineContext())
    engine.load([mini_compile(contract_path.read_text())])
    return engine


def corpus_lines(name: str) -> list[bytes]:
    """Corpus lines, joining continuation lines the way the edge does."""
    from veyra_common.framing import split_lines

    return [framed.raw for framed in split_lines((CORPUS / name).read_bytes())]


def summarize(result: Any, raw: bytes) -> dict[str, Any]:
    """The part of a normalized event worth pinning in a snapshot."""
    offsets = result.ulpf.get("field_offsets", {})
    return {
        "tier": result.tier,
        "conformance": result.conformance,
        "category": result.category,
        "class_uid": result.ocsf.get("class_uid"),
        "activity_id": result.ocsf.get("activity_id"),
        "type_uid": result.ocsf.get("type_uid"),
        "status_id": result.ocsf.get("status_id"),
        "severity_id": result.ocsf.get("severity_id"),
        "parse_path": result.parse_path,
        "template_id": (result.ulpf.get("template") or {}).get("id"),
        "mapped": {
            path: raw[span[0] : span[1]].decode("utf-8", errors="replace")
            for path, span in sorted(offsets.items())
        },
        "derived_fields": dict(sorted((result.ulpf.get("derived_fields") or {}).items())),
        # Where the event time came from: "event" means we parsed the source's own timestamp,
        # "received" means we fell back to arrival. A regression here is easy to miss otherwise.
        "time_source": (result.ulpf.get("time") or {}).get("source"),
        "year_inferred": (result.ulpf.get("time") or {}).get("year_inferred"),
        "observables": [o["value"] for o in result.ocsf.get("observables", [])],
        "unmapped_keys": sorted(result.ocsf.get("unmapped", {})),
        "dlq_reason": None if result.dlq is None else result.dlq.reason_code,
    }


def build_snapshot(name: str) -> list[dict[str, Any]]:
    case = CASES[name]
    engine = engine_for(case[0])
    out: list[dict[str, Any]] = []
    for index, raw in enumerate(corpus_lines(name)):
        envelope = make_envelope(raw, case, index)
        result = engine.normalize(envelope)
        out.append(summarize(result, raw))
    return out


@pytest.mark.parametrize("name", sorted(CASES))
def test_golden_snapshot(name: str) -> None:
    """Every corpus line normalizes exactly as recorded."""
    expected_path = EXPECTED / f"{name}.json"
    assert expected_path.exists(), f"missing snapshot; run this file with --update ({name})"
    expected = json.loads(expected_path.read_text())
    actual = build_snapshot(name)
    assert len(actual) == len(expected), f"{name}: line count changed"
    for index, (got, want) in enumerate(zip(actual, expected, strict=True)):
        assert got == want, f"{name} line {index} changed:\n got={got}\nwant={want}"


# Files whose contract covers every line in the corpus. linux_sshd is deliberately absent:
# the seeded library contract has three templates, and real sshd chatter (pam_unix, publickey,
# disconnects, sudo) does not match them — those are exactly the shapes C3's drift detection is
# meant to surface, so they must land in the DLQ rather than be forced to tier 1 here.
FULLY_COVERED = ("acme_ngfw_cef.log", "authsrv_t1_ok.log", "authsrv_t2_session.log")


@pytest.mark.parametrize("name", FULLY_COVERED)
def test_covered_corpus_files_are_all_tier_one(name: str) -> None:
    case = CASES[name]
    engine = engine_for(case[0])
    tiers: dict[int, int] = {}
    for index, raw in enumerate(corpus_lines(name)):
        result = engine.normalize(make_envelope(raw, case, index))
        tiers[result.tier] = tiers.get(result.tier, 0) + 1
    assert set(tiers) == {1}, f"{name}: expected all tier 1, got {tiers}"


def test_sshd_auth_lines_reach_tier_one_and_the_rest_are_never_dropped() -> None:
    """What the seeded contract covers must be tier 1; what it does not must still be emitted.

    The first six corpus lines are the brute-force burst Wazuh rule 100111 fires on, so those
    specifically have to normalize with the attacker IP mapped.
    """
    name = "linux_sshd.log"
    case = CASES[name]
    engine = engine_for(case[0])
    lines = corpus_lines(name)

    for index, raw in enumerate(lines[:6]):
        result = engine.normalize(make_envelope(raw, case, index))
        assert result.tier == 1, f"burst line {index} must be tier 1, got {result.tier}"
        assert result.ocsf["class_uid"] == 3002
        assert result.ocsf["status_id"] == 2
        assert result.ocsf["src_endpoint"]["ip"] == "45.12.3.9"

    covered = uncovered = 0
    for index, raw in enumerate(lines):
        result = engine.normalize(make_envelope(raw, case, index))
        if result.tier == 1:
            covered += 1
            continue
        uncovered += 1
        # Never dropped: every one of these still carries its raw bytes and a DLQ reason.
        assert result.dlq is not None
        assert result.dlq.reason_code == "no_template_match"
        assert result.ocsf["raw_data"]
    assert covered >= 10, f"only {covered} sshd lines matched the seeded contract"
    assert uncovered > 0, "the corpus should still contain shapes the seed does not cover"


@pytest.mark.parametrize("name", sorted(CASES))
def test_at_least_twenty_samples_each(name: str) -> None:
    """A3 task 6 asks for >= 20 samples per contract, so the goldens actually cover it."""
    assert len(corpus_lines(name)) >= 20


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_offset_slices_its_own_value(name: str) -> None:
    """P4, asserted per line: the offset must yield exactly the mapped value."""
    case = CASES[name]
    engine = engine_for(case[0])
    for index, raw in enumerate(corpus_lines(name)):
        result = engine.normalize(make_envelope(raw, case, index))
        checks = provenance_check(result.ocsf, raw)
        if result.tier >= 3:
            # Nothing was mapped, so there is nothing to locate — A4's generic extractor is
            # what puts offsets on these.
            continue
        assert checks, f"{name} line {index} reached tier {result.tier} but mapped nothing"
        bad = [(c.ocsf_path, c.reason) for c in checks if not c.ok]
        assert not bad, f"{name} line {index}: {bad}"


@pytest.mark.parametrize("name", sorted(CASES))
def test_events_validate_against_the_norm_event_model(name: str) -> None:
    """What B indexes and the router ships must satisfy IF-NORM-EVENT."""
    case = CASES[name]
    engine = engine_for(case[0])
    for index, raw in enumerate(corpus_lines(name)):
        result = engine.normalize(make_envelope(raw, case, index))
        NormEvent.model_validate(result.ocsf)


def test_authsrv_t3_is_not_covered_by_version_one() -> None:
    """The demo's premise: T3 is a shape v1 has never seen, so it must not reach tier 1."""
    engine = engine_for(TEST_CONTRACTS / "authsrv.yaml")
    case = CASES["authsrv_t1_ok.log"]
    for index, raw in enumerate(corpus_lines("authsrv_t3_failed.log")):
        result = engine.normalize(make_envelope(raw, case, index))
        assert result.tier >= 3, "T3 must not match authsrv@1"
        assert result.dlq is not None
        assert result.dlq.reason_code == "no_template_match"


def test_authsrv_v2_upgrades_t3_to_tier_one() -> None:
    """And version 2 — the contract the drift loop produces — must fix exactly that."""
    engine = engine_for(TEST_CONTRACTS / "authsrv_v2.yaml")
    case = CASES["authsrv_t1_ok.log"]
    lines = corpus_lines("authsrv_t3_failed.log")
    assert lines, "T3 corpus is empty"
    for index, raw in enumerate(lines):
        result = engine.normalize(make_envelope(raw, case, index))
        assert result.tier == 1, f"line {index} did not upgrade: {result.conformance}"
        assert result.ocsf["status_id"] == 2, "a FAILED login is a failure"
        assert result.ocsf["class_uid"] == 3002
        assert result.ulpf["template"]["id"] == "auth_failed"
        # The multi-line event keeps its trace, and the attacker IP is located in the raw bytes.
        assert "attempts" in result.ocsf["unmapped"]
        span = result.ulpf["field_offsets"]["src_endpoint.ip"]
        assert raw[span[0] : span[1]] == result.ocsf["src_endpoint"]["ip"].encode()


def test_garbage_never_crashes_and_always_lands_in_the_dlq() -> None:
    """AC3: `garbage.bin` must produce tier 4 with a reason, never an exception."""
    engine = engine_for(SEED_CONTRACTS / "linux_sshd.yaml")
    case = CASES["linux_sshd.log"]
    raw_blob = (CORPUS / "garbage.bin").read_bytes()
    chunks = [chunk for chunk in raw_blob.split(b"\n") if chunk] + [raw_blob]
    for index, raw in enumerate(chunks):
        result = engine.normalize(make_envelope(raw, case, index))
        assert result.tier == 4, f"chunk {index} should be unparseable, got tier {result.tier}"
        assert result.dlq is not None
        assert result.dlq.reason_code in {
            "no_template_match",
            "no_contract",
            "decode_error",
            "engine_crash",
            "size_exceeded",
            "budget_exceeded",
        }


def test_ot_historian_is_unregistered_and_survives() -> None:
    """No contract covers it; it must still produce a complete event (P2)."""
    engine = Engine(EngineContext())  # nothing loaded at all
    case = CASES["linux_sshd.log"]
    for index, raw in enumerate(corpus_lines("ot_historian.log")):
        result = engine.normalize(make_envelope(raw, case, index))
        assert result.dlq is not None
        assert result.dlq.reason_code == "no_contract"
        assert result.ocsf["raw_data"], "the raw text must survive"
        NormEvent.model_validate(result.ocsf)


def main() -> int:
    """`--update` regenerates the snapshots."""
    if "--update" not in sys.argv:
        print(__doc__)
        return 1
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for name in sorted(CASES):
        snapshot = build_snapshot(name)
        target = EXPECTED / f"{name}.json"
        target.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        tiers = sorted({row["tier"] for row in snapshot})
        print(f"wrote {target.name}: {len(snapshot)} lines, tiers {tiers}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
