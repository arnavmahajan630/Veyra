"""Internal endpoints (docker network only; C1): reset, demo last-key, drift upsert stub."""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session as DbSession

from control_api.context import AppContext, get_ctx
from control_api.inventory import rewrite_inventory
from control_api.messages import republish_all
from control_api.publisher import PublishError
from control_api.seed import reset_state

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
    try:
        summary = reset_state(ctx.engine, ctx.cfg, ctx.clock(), scenario=body.scenario)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    with DbSession(ctx.engine) as db:
        try:
            republish_all(db, ctx.publisher, ctx.docs, ctx.now())
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


@router.post("/drift", status_code=202)
def drift(payload: dict[str, Any]) -> dict[str, str]:
    return {"status": "accepted", "note": "the drift inbox lands in C3"}
