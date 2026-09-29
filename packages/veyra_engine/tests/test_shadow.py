"""A5: a candidate version runs beside the active one and reports the difference.

The demo's Beat 4 rests on this being trustworthy in both directions. A reviewer looking at a canary
needs to believe two things: that the candidate cannot affect what was delivered, and that
`regressions` really lists everything the new version stopped claiming. A shadow diff that misses a
lost field is worse than no shadow at all, because it is the thing an approver relies on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_engine import Engine, EngineContext, mini_compile

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
CONTRACTS = Path(__file__).resolve().parent / "contracts"
RECEIVED = "2026-09-26T14:10:00.000000000Z"
SOURCE = "src_authsrv_01"


def compiled(name: str) -> dict:
    return mini_compile((CONTRACTS / name).read_text())


def t3_lines() -> list[bytes]:
    from veyra_common.framing import split_lines

    return [event.raw for event in split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())]


def envelope(raw: bytes, index: int = 0) -> Envelope:
    return stamp(
        raw,
        collector_id="edge-dmz-01",
        transport="syslog_tcp",
        framing_method="multiline_join",
        source_id=SOURCE,
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
        event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
        received_time=RECEIVED,
    )


def engine_with(active: str | None, candidate: str | None = None, **ctx: object) -> Engine:
    engine = Engine(EngineContext(**ctx))  # type: ignore[arg-type]
    if active is not None:
        engine.load([compiled(active)])
    if candidate is not None:
        compiled_candidate = compiled(candidate)
        engine.set_candidate(compiled_candidate, str(compiled_candidate["contract"]))
    return engine


# ---------------------------------------------------------------- no candidate
def test_no_candidate_means_no_shadow_work() -> None:
    """The common case must cost nothing: no candidate, no second parse, no record."""
    engine = engine_with("authsrv.yaml")
    event = envelope(t3_lines()[0])
    assert engine.shadow(event, engine.normalize(event)) is None


def test_a_candidate_for_another_source_is_not_shadowed() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    other = stamp(
        b"<134>Sep 26 14:05:11 fw01 app[233]: unrelated",
        collector_id="edge",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_somebody_else",
        received_time=RECEIVED,
    )
    assert engine.shadow(other, engine.normalize(other)) is None


# ---------------------------------------------------------------- AC1
def test_the_canary_upgrades_the_t3_event_without_touching_the_output() -> None:
    """AC1: candidate tier 1 beside active tier 3, and the delivered event is still tier 3."""
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    event = envelope(t3_lines()[0])
    active = engine.normalize(event)
    assert active.tier == 3, "authsrv@1 has never seen this shape"

    diff = engine.shadow(event, active)
    assert diff is not None
    assert diff.active_tier == 3
    assert diff.candidate_tier == 1
    assert diff.contract_id == "authsrv"
    assert diff.active_ref == "authsrv@1"
    assert diff.candidate_ref == "authsrv@2"
    assert diff.skipped is None
    assert not diff.regressions, f"an upgrade must not report regressions: {diff.regressions}"
    # The paths the new version adds are what a reviewer approves on.
    assert "user.name" in diff.changed_fields
    assert "src_endpoint.ip" in diff.changed_fields

    # And the output itself is untouched: the shadow run must not mutate the delivered event.
    assert active.tier == 3
    assert active.ocsf["class_uid"] == 0
    assert engine.normalize(event).ocsf == active.ocsf


def test_every_t3_event_shadows_consistently() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    lines = t3_lines()
    assert lines
    for index, raw in enumerate(lines):
        event = envelope(raw, index)
        diff = engine.shadow(event, engine.normalize(event))
        assert diff is not None and diff.skipped is None
        assert (diff.active_tier, diff.candidate_tier) == (3, 1), f"line {index}"


# ---------------------------------------------------------------- regressions
def test_a_candidate_that_loses_a_field_reports_it_as_a_regression() -> None:
    """The case an approver must never miss: tier stays 1, but a mapped field disappears."""
    active = compiled("authsrv_v2.yaml")
    stripped = compiled("authsrv_v2.yaml")
    stripped["version"] = 3
    for template in stripped["templates"]:
        template["map"] = [
            entry for entry in template["map"] if entry.get("ocsf_path") != "src_endpoint.ip"
        ]

    engine = Engine(EngineContext())
    engine.load([active])
    engine.set_candidate(stripped, str(stripped["contract"]))

    event = envelope(t3_lines()[0])
    delivered = engine.normalize(event)
    assert delivered.tier == 1
    diff = engine.shadow(event, delivered)
    assert diff is not None
    assert diff.candidate_tier == 1, "the tier is fine — that is what makes this dangerous"
    assert "src_endpoint.ip" in diff.regressions


def test_a_worse_tier_is_itself_a_regression() -> None:
    """A candidate that no longer matches at all: tier 1 -> 3, recorded as a tier regression."""
    engine = Engine(EngineContext())
    engine.load([compiled("authsrv_v2.yaml")])
    broken = compiled("authsrv_v2.yaml")
    broken["version"] = 3
    broken["templates"] = []
    engine.set_candidate(broken, str(broken["contract"]))

    event = envelope(t3_lines()[0])
    delivered = engine.normalize(event)
    diff = engine.shadow(event, delivered)
    assert diff is not None
    assert diff.candidate_tier > diff.active_tier
    assert any(entry.startswith("tier:") for entry in diff.regressions), diff.regressions


def test_changed_fields_compares_values_not_offsets() -> None:
    """Identical contracts produce identical values, so nothing is 'changed'.

    This is why the comparison is value-based: two compilations of the same YAML can differ in
    which fields carry offsets without differing in any datum a SIEM rule would read.
    """
    engine = Engine(EngineContext())
    active = compiled("authsrv_v2.yaml")
    twin = compiled("authsrv_v2.yaml")
    twin["version"] = 3
    engine.load([active])
    engine.set_candidate(twin, str(twin["contract"]))

    event = envelope(t3_lines()[0])
    diff = engine.shadow(event, engine.normalize(event))
    assert diff is not None
    assert diff.changed_fields == []
    assert diff.regressions == []


# ---------------------------------------------------------------- budget
def test_the_shadow_has_its_own_budget_and_gives_up_quietly() -> None:
    """A slow canary must be skipped, not allowed to delay the event a customer is waiting for."""
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml", shadow_budget_us=1)
    event = envelope(t3_lines()[0])
    active = engine.normalize(event)
    diff = engine.shadow(event, active)
    assert diff is not None
    assert diff.skipped is not None and diff.skipped.startswith("budget_exceeded")
    # The comparison is abandoned, but the record still says which candidate it was about.
    assert diff.candidate_ref == "authsrv@2"
    assert active.tier == 3, "the delivered event is unaffected"


def test_a_shadow_budget_of_zero_means_no_limit() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml", shadow_budget_us=0)
    event = envelope(t3_lines()[0])
    diff = engine.shadow(event, engine.normalize(event))
    assert diff is not None and diff.skipped is None
    assert diff.candidate_tier == 1


def test_the_event_budget_is_not_spent_on_the_shadow() -> None:
    """Two separate budgets: the active run's timings must not include the candidate's work."""
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    event = envelope(t3_lines()[0])
    active = engine.normalize(event)
    before = active.timings_us["total"]
    engine.shadow(event, active)
    assert active.timings_us["total"] == before


# ---------------------------------------------------------------- determinism
def test_the_shadow_diff_is_deterministic() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    event = envelope(t3_lines()[0])
    first = engine.shadow(event, engine.normalize(event))
    second = engine.shadow(event, engine.normalize(event))
    assert first == second


@pytest.mark.parametrize("candidate", ["authsrv.yaml", "authsrv_v2.yaml"])
def test_a_shadow_record_validates_as_if_shadow(candidate: str) -> None:
    """Whatever the diff says, it has to fit the record B1 indexes."""
    from veyra_common.models import ShadowRecord

    engine = Engine(EngineContext())
    engine.load([compiled("authsrv.yaml")])
    compiled_candidate = compiled(candidate)
    compiled_candidate["version"] = 9
    engine.set_candidate(compiled_candidate, str(compiled_candidate["contract"]))

    event = envelope(t3_lines()[0])
    diff = engine.shadow(event, engine.normalize(event))
    assert diff is not None
    record = ShadowRecord(
        event_uid=event.event_uid,
        contract_id=diff.contract_id,
        active_ref=diff.active_ref,
        candidate_ref=diff.candidate_ref,
        active_tier=diff.active_tier,
        candidate_tier=diff.candidate_tier,
        changed_fields=diff.changed_fields,
        regressions=diff.regressions,
        produced_at=RECEIVED,
    )
    assert record.candidate_ref.endswith("@9")
