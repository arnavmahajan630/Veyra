"""Backtest (C2): what would a candidate version do to real stored events?

Two collaborators, Protocols in ``evidence.py`` (fakes in tests, real ones in the service):

- ``EventIndex`` answers "which events have template sig X?", with each event's latest
  revision and ``raw_ref`` (IF-API-EVIDENCE ``GET /lineage/templates/{sig}/events``).
- ``RawStore`` turns ``raw_ref``s back into IF-ENVELOPEs.

The engine work itself is ``veyra_engine.backtest`` in-process, the same library the
normalizer runs, so the result is what production would do.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import asdict
from typing import Any

from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.evidence import EventIndex, EventRef, EvidenceUnavailable, RawStore
from control_api.tables import DriftItem
from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_engine import backtest as engine_backtest

OPEN_DRIFT = ("open", "drafting", "draft_ready")
SAMPLE_RECEIVED = "2026-09-26T14:10:00.000000000Z"


def drift_sigs(
    db: DbSession, source_ids: Iterable[str], states: Sequence[str] = OPEN_DRIFT
) -> list[str]:
    """The template sigs of drift items on these sources, with their related sigs."""
    query = select(DriftItem).where(
        col(DriftItem.source_id).in_(list(source_ids)), col(DriftItem.state).in_(list(states))
    )
    sigs: list[str] = []
    for item in db.exec(query.order_by(col(DriftItem.drift_id))).all():
        for sig in [item.template_sig, *json.loads(item.related_sigs_json)]:
            if sig not in sigs:
                sigs.append(sig)
    return sigs


def find_events(index: EventIndex, sigs: Sequence[str], limit: int) -> list[EventRef]:
    """Up to ``limit`` distinct events across the sigs, in sig order."""
    refs: list[EventRef] = []
    seen: set[str] = set()
    for sig in sigs:
        if len(refs) >= limit:
            break
        for ref in index.template_events(sig, limit=limit - len(refs)):
            if ref.event_uid not in seen:
                seen.add(ref.event_uid)
                refs.append(ref)
    return refs[:limit]


def sample_envelopes(samples: Sequence[str], compiled: dict[str, Any]) -> list[Envelope]:
    """Pasted samples (onboarding) as in-memory envelopes of the contract's first source."""
    sources = compiled.get("sources") or ["unregistered"]
    return [
        stamp(
            text.encode("utf-8"),
            collector_id="backtest",
            transport="http_hec_raw",
            framing_method="http_body",
            tenant_id=str(compiled.get("tenant") or "unassigned"),
            source_id=str(sources[0]),
            vendor=str(compiled.get("contract", "custom")),
            event_uid=f"00000000-0000-7000-8000-{i:012d}",
            received_time=SAMPLE_RECEIVED,
        )
        for i, text in enumerate(samples)
    ]


def run_backtest(
    active: dict[str, Any] | None,
    candidate: dict[str, Any],
    *,
    index: EventIndex | None,
    raw: RawStore | None,
    sigs: Sequence[str],
    limit: int,
    samples: Sequence[str] = (),
) -> dict[str, Any]:
    """Backtest the candidate against stored events for ``sigs`` plus pasted ``samples``.

    Never raises for a missing collaborator: the result says what it could not reach, so
    a submission still succeeds while evidence-api is down.
    """
    started = time.perf_counter()
    envelopes = sample_envelopes(samples, candidate)
    error: str | None = None
    found = 0
    if sigs:
        if index is None or raw is None:
            error = "no event index configured"
        else:
            try:
                refs = find_events(index, sigs, max(limit - len(envelopes), 0))
                found = len(refs)
                envelopes += raw.envelopes(refs)
            except EvidenceUnavailable as exc:
                error = str(exc)
    result = engine_backtest(active, candidate, envelopes)
    out = asdict(result)
    # JSON keys are strings; say so explicitly rather than let json.dumps coerce them.
    out["tier_before"] = {str(k): v for k, v in sorted(result.tier_before.items())}
    out["tier_after"] = {str(k): v for k, v in sorted(result.tier_after.items())}
    out.update(
        sigs=list(sigs),
        samples=len(samples),
        events_found=found,
        raw_missing=found - (len(envelopes) - len(samples)),
        error=error,
        ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return out
