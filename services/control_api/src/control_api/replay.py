"""Replay jobs (C2): re-process stored events with the contract that is active now.

``POST /replay`` records a job and hands ``run_job`` to ``ctx.spawn`` (a daemon thread in
the service, inline in tests). The job:

1. resolves its events: ``template_sigs`` through the event index, filtered by
   ``source_id`` and the ``from``/``to`` window, capped at ``VEYRA_REPLAY_MAX``;
2. fetches each envelope and publishes it to ``replay.raw`` with the IF-ENVELOPE
   ``replay`` block ``{job_id, revision: latest + 1, supersedes: "<uid>@<latest>"}``;
3. watches ``lineage`` for records carrying its ``replay_job_id`` until every published
   event is normalized (``done``) or ``VEYRA_REPLAY_TIMEOUT_S`` passes (``timed_out``).

Every change is saved and sent as an SSE ``replay`` event
``{job_id, contract_id, status, total, published, normalized, detail}``.
States: pending → publishing → normalizing → done | timed_out | failed.
"""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session as DbSession

from control_api.backtest import find_events
from control_api.context import AppContext
from control_api.evidence import EventRef, EvidenceUnavailable
from control_api.tables import ReplayJob
from veyra_common.models import ReplayEnvelope
from veyra_common.topics import TOPIC_REPLAY_RAW

FINAL = ("done", "timed_out", "failed")


def job_event(job: ReplayJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "contract_id": job.contract_id,
        "status": job.state,
        "total": job.total,
        "published": job.published,
        "normalized": job.normalized,
        "detail": job.detail,
    }


def _update(ctx: AppContext, job_id: str, **fields: Any) -> ReplayJob:
    with DbSession(ctx.engine) as db:
        job = db.get(ReplayJob, job_id)
        assert job is not None
        for name, value in fields.items():
            setattr(job, name, value)
        if job.state in FINAL and job.finished_at is None:
            job.finished_at = ctx.now()
        db.add(job)
        db.commit()
        db.refresh(job)
    ctx.hub.publish("replay", job_event(job))
    return job


def _select(refs: list[EventRef], params: dict[str, Any]) -> list[EventRef]:
    source, start, end = params.get("source_id"), params.get("from"), params.get("to")
    return [
        r
        for r in refs
        if (source is None or r.source_id == source)
        and (start is None or r.produced_at >= start)
        and (end is None or r.produced_at <= end)
    ]


def run_job(ctx: AppContext, job_id: str) -> None:
    since_ms = ctx.clock() // 1_000_000
    with DbSession(ctx.engine) as db:
        job = db.get(ReplayJob, job_id)
        assert job is not None
        params: dict[str, Any] = json.loads(job.params_json)
    if ctx.index is None or ctx.raw is None:
        _update(ctx, job_id, state="failed", detail="no event index configured")
        return
    try:
        refs = _select(find_events(ctx.index, params["template_sigs"], ctx.cfg.replay_max), params)
        _update(ctx, job_id, state="publishing", total=len(refs))
        envelopes = {e.event_uid: e for e in ctx.raw.envelopes(refs)}
    except EvidenceUnavailable as exc:
        _update(ctx, job_id, state="failed", detail=str(exc))
        return

    published = 0
    for ref in refs:
        envelope = envelopes.get(ref.event_uid)
        if envelope is None:
            continue
        replayed = ReplayEnvelope.model_validate(
            {
                **envelope.model_dump(mode="json"),
                "replay": {
                    "job_id": job_id,
                    "revision": ref.revision + 1,
                    "supersedes": f"{ref.event_uid}@{ref.revision}",
                },
            }
        )
        ctx.producer.produce(
            TOPIC_REPLAY_RAW,
            value=replayed.model_dump_json().encode("utf-8"),
            key=envelope.source_id,
        )
        published += 1
    missing = len(refs) - published
    detail = f"{missing} event(s) no longer in raw storage" if missing else ""
    if ctx.producer.flush(ctx.cfg.control_publish_timeout_s):
        _update(ctx, job_id, state="failed", detail="replay.raw did not acknowledge")
        return
    if published == 0:
        _update(ctx, job_id, state="failed", detail=detail or "no events to replay")
        return
    _update(ctx, job_id, state="normalizing", published=published, detail=detail)

    if ctx.watcher is None:
        _update(ctx, job_id, state="timed_out", detail="normalization is not being watched")
        return

    def progress(normalized: int) -> None:
        _update(ctx, job_id, normalized=normalized)

    try:
        normalized = ctx.watcher.watch(
            job_id,
            since_ms=since_ms,
            expected=published,
            timeout_s=ctx.cfg.replay_timeout_s,
            on_progress=progress,
        )
    except EvidenceUnavailable as exc:
        _update(ctx, job_id, state="failed", detail=str(exc))
        return
    state = "done" if normalized >= published else "timed_out"
    _update(ctx, job_id, state=state, normalized=normalized)
