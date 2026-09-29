"""A5 AC2: `backtest()` — what a candidate version would do to events already stored.

This is the number the demo puts on screen in Beat 4 ("all of them, tier 3 → tier 1"), and both
C2's lifecycle and C4's drafter call this function in-process, so its shape is a cross-track
interface: the keys asserted here are the ones `console/src/api/types.ts` is built against.
"""

from __future__ import annotations

import time
from pathlib import Path

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope
from veyra_engine import backtest, mini_compile
from veyra_engine.engine import BACKTEST_BUDGET_US

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
CONTRACTS = Path(__file__).resolve().parent / "contracts"
RECEIVED = "2026-09-26T14:10:00.000000000Z"


def compiled(name: str) -> dict:
    return mini_compile((CONTRACTS / name).read_text())


def t3_envelopes() -> list[Envelope]:
    events = split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())
    return [
        stamp(
            event.raw,
            collector_id="edge-dmz-01",
            transport="syslog_tcp",
            framing_method="multiline_join",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
            event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
            received_time=RECEIVED,
        )
        for index, event in enumerate(events)
    ]


# ---------------------------------------------------------------- AC2
def test_ac2_the_t3_corpus_upgrades_wholesale_and_fast() -> None:
    """AC2: every stored T3 event upgrades, nothing regresses, well inside 200 ms.

    The AC says "8 T3 envelopes"; the corpus as generated holds **14** logical events once
    continuation lines are joined (`gen_corpus.py` grew it after the phase file was written). The
    substance is unchanged and stricter: all of them, not eight of them.
    """
    envelopes = t3_envelopes()
    assert len(envelopes) == 14, f"the T3 corpus changed shape: {len(envelopes)} events"

    started = time.perf_counter()
    result = backtest(compiled("authsrv.yaml"), compiled("authsrv_v2.yaml"), envelopes)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert result.n == len(envelopes)
    assert result.upgraded == len(envelopes), (
        f"expected every event to improve: {result.tier_before} -> {result.tier_after}"
    )
    assert result.regressed == 0
    assert result.unchanged == 0
    assert result.tier_before == {3: len(envelopes)}
    assert result.tier_after == {1: len(envelopes)}
    assert elapsed_ms < 200, f"AC2 allows 200 ms, took {elapsed_ms:.0f} ms"


def test_field_coverage_says_how_consistently_a_path_is_produced() -> None:
    """The number a reviewer asks for: does it map user.name in all of them, or just the sample?"""
    result = backtest(compiled("authsrv.yaml"), compiled("authsrv_v2.yaml"), t3_envelopes())
    assert result.field_coverage["user.name"] == 100.0
    assert result.field_coverage["src_endpoint.ip"] == 100.0
    assert result.field_coverage["dst_endpoint.ip"] == 100.0
    # Coverage is a percentage of events, so nothing can exceed 100.
    assert all(0 < pct <= 100 for pct in result.field_coverage.values())
    # And it describes the candidate, not the active version: tier 3 produced no user.name at all.
    before = backtest(None, compiled("authsrv.yaml"), t3_envelopes())
    assert "user.name" not in before.field_coverage


def test_examples_keep_the_keys_the_console_renders() -> None:
    result = backtest(compiled("authsrv.yaml"), compiled("authsrv_v2.yaml"), t3_envelopes())
    assert result.examples, "a backtest with events must show examples"
    assert len(result.examples) <= 20
    for example in result.examples:
        assert set(example) == {
            "event_uid",
            "before_tier",
            "after_tier",
            "changed_fields",
            "provenance_ok",
        }
        assert example["provenance_ok"] is True, "every offset the candidate claims must hold"
        assert "user.name" in example["changed_fields"]


def test_no_active_version_means_everything_is_measured_against_tier_three() -> None:
    """Onboarding's case: there is no active contract yet, so 'before' is the generic extractor."""
    envelopes = t3_envelopes()
    result = backtest(None, compiled("authsrv_v2.yaml"), envelopes)
    assert result.tier_before == {3: len(envelopes)}
    assert result.upgraded == len(envelopes)


def test_an_empty_event_list_is_not_an_error() -> None:
    """A submission with nothing stored yet must still succeed — C2 shows the panel either way."""
    result = backtest(compiled("authsrv.yaml"), compiled("authsrv_v2.yaml"), [])
    assert result.n == 0
    assert result.upgraded == result.regressed == result.unchanged == 0
    assert result.field_coverage == {}
    assert result.examples == []


def test_a_candidate_that_stops_matching_is_reported_as_regressed() -> None:
    broken = compiled("authsrv_v2.yaml")
    broken["templates"] = []
    envelopes = t3_envelopes()
    result = backtest(compiled("authsrv_v2.yaml"), broken, envelopes)
    assert result.regressed == len(envelopes)
    assert result.upgraded == 0
    assert result.tier_before == {1: len(envelopes)}
    assert result.tier_after == {3: len(envelopes)}


# ---------------------------------------------------------------- budget
def test_the_backtest_budget_is_generous_by_default() -> None:
    """A stored event may be far bigger than the realtime profile allows. Not a regression."""
    assert BACKTEST_BUDGET_US >= 50 * 1000
    big = stamp(
        b"user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1 " + b"a=b " * 4000,
        collector_id="edge",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_authsrv_01",
        received_time=RECEIVED,
    )
    result = backtest(compiled("authsrv.yaml"), compiled("authsrv_v2.yaml"), [big])
    assert 4 not in result.tier_after, "a big event must not be reported as budget_exceeded"


def test_the_budget_can_be_overridden_and_disabled() -> None:
    envelopes = t3_envelopes()
    unlimited = backtest(None, compiled("authsrv_v2.yaml"), envelopes, budget_us=0)
    assert unlimited.upgraded == len(envelopes), "0 must mean no limit, not 'already over'"
    # A cruelly small budget degrades the result rather than raising — the caller still gets a
    # BacktestResult, which is what keeps a submission from failing outright.
    tight = backtest(None, compiled("authsrv_v2.yaml"), envelopes, budget_us=1)
    assert tight.n == len(envelopes)
