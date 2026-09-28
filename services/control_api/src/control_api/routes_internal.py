"""Internal endpoints (docker network only): reset, demo last-key (C1), drift upsert (C3)."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session as DbSession

from control_api.context import AppContext, get_ctx, get_db
from control_api.drift import DriftIn, drift_event, upsert
from control_api.inventory import rewrite_inventory
from control_api.messages import published_keys, republish_all
from control_api.publisher import PublishError
from control_api.seed import reset_state

log = logging.getLogger(__name__)
router = APIRouter(prefix="/internal", tags=["internal"])


class ResetIn(BaseModel):
    scenario: str = "sih_main"


class ResetOut(BaseModel):
    ok: bool
    seconds: float
    tenants: int
    users: int
    sources: int
    contracts: int


class LastKeyOut(BaseModel):
    key_id: str
    secret: str
    source_id: str


@router.post("/reset", response_model=ResetOut)
def reset(body: ResetIn, ctx: AppContext = Depends(get_ctx)) -> ResetOut:
    started = time.monotonic()
    with DbSession(ctx.engine) as db:
        before = published_keys(db, ctx.docs)
    try:
        summary = reset_state(ctx.engine, ctx.cfg, ctx.clock(), scenario=body.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with DbSession(ctx.engine) as db:
        try:
            republish_all(db, ctx.publisher, ctx.docs, ctx.now(), stale=before)
        except PublishError as exc:
            raise HTTPException(
                status_code=503, detail=f"control topic unavailable: {exc}"
            ) from exc
        rewrite_inventory(db, ctx.cfg)
        ctx.audit.record(
            db, actor="demo-engine", role="system", action="reset", target=body.scenario
        )
        db.commit()
    ctx.last_key.forget()
    if ctx.drift_reset is not None:  # C3: the drift worker forgets its groups too
        try:
            ctx.drift_reset()
        except Exception:
            log.exception("drift-worker reset failed; its groups survive until it restarts")
    ctx.hub.publish("source", {"event": "reset", "scenario": body.scenario})
    return ResetOut(ok=True, seconds=round(time.monotonic() - started, 3), **asdict(summary))


@router.get("/demo/last-key", response_model=LastKeyOut)
def last_key(ctx: AppContext = Depends(get_ctx)) -> LastKeyOut:
    if not ctx.cfg.demo_mode:
        raise HTTPException(status_code=404, detail="demo mode is off")
    issued = ctx.last_key.get()
    if issued is None:
        raise HTTPException(status_code=404, detail="no key issued since the last reset")
    return LastKeyOut(**asdict(issued))


@router.post("/drift")
def drift(
    body: DriftIn, ctx: AppContext = Depends(get_ctx), db: DbSession = Depends(get_db)
) -> dict[str, str | bool]:
    """The drift worker's upsert (C3). An unknown source is 404; the worker drops it."""
    result = upsert(ctx, db, body)
    if result is None:
        raise HTTPException(status_code=404, detail=f"source {body.source_id} not found")
    item, created = result
    db.commit()
    db.refresh(item)
    ctx.hub.publish("drift", drift_event(item, created=created))
    return {"drift_id": item.drift_id, "state": item.state, "created": created}
