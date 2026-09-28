"""Drafts (IF-API-CONTROL): draft a drift item, read, edit and submit a draft (C4)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session as DbSession

from control_api.auth import Principal, current_principal, require
from control_api.context import AppContext, get_ctx, get_db
from control_api.drafts import (
    DraftEdit,
    DraftOut,
    EditRejected,
    apply_edit,
    draft_event,
    draft_out,
    payload,
    start_drift_draft,
)
from control_api.onboarding import AnalyzeIn, analyze
from control_api.registry import ACTORS, WRITERS
from control_api.routes_contracts import SubmitIn, VersionDetail, submit_contract
from control_api.routes_drift import load_item
from control_api.tables import Draft, Source

router = APIRouter(tags=["drafts"])
Mode = Literal["live", "cache", "live_then_cache", "heuristic"]


class DraftStart(BaseModel):
    mode: Mode | None = None


class DraftStarted(BaseModel):
    draft_id: str


def load_draft(db: DbSession, principal: Principal, draft_id: str) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None or not principal.can_see(draft.tenant_id):
        raise HTTPException(status_code=404, detail=f"draft {draft_id} not found")
    return draft


@router.post("/drift/{drift_id}/draft", response_model=DraftStarted, status_code=202)
def draft_drift(
    drift_id: str,
    body: DraftStart | None = None,
    principal: Principal = Depends(require(*ACTORS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> DraftStarted:
    item = load_item(db, principal, drift_id)
    if item.state not in ("open", "draft_ready"):
        raise HTTPException(status_code=409, detail=f"drift item is {item.state}")
    draft = start_drift_draft(
        ctx, db, item, actor=principal.email, mode=(body or DraftStart()).mode
    )
    return DraftStarted(draft_id=draft.draft_id)


@router.get("/drafts/{draft_id}", response_model=DraftOut)
def get_draft(
    draft_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> DraftOut:
    return draft_out(load_draft(db, principal, draft_id))


@router.patch("/drafts/{draft_id}", response_model=DraftOut)
def edit_draft(
    draft_id: str,
    body: DraftEdit,
    principal: Principal = Depends(require(*ACTORS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> DraftOut:
    draft = load_draft(db, principal, draft_id)
    if draft.state != "ready":
        raise HTTPException(status_code=409, detail=f"draft is {draft.state}")
    try:
        apply_edit(ctx, db, draft, body)
    except EditRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(draft)
    ctx.hub.publish("draft", draft_event(draft))
    return draft_out(draft)


@router.post("/drafts/{draft_id}/submit", response_model=VersionDetail, status_code=201)
def submit_draft(
    draft_id: str,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    draft = load_draft(db, principal, draft_id)
    if draft.state != "ready":
        raise HTTPException(status_code=409, detail=f"draft is {draft.state}")
    version = submit_contract(
        SubmitIn(yaml=payload(draft)["yaml"], draft_id=draft.draft_id), principal, ctx, db
    )
    draft.state, draft.updated_at = "submitted", ctx.now()
    db.add(draft)
    db.commit()
    ctx.hub.publish("draft", draft_event(draft))
    return version


@router.post("/onboarding/analyze")
def onboarding_analyze(
    body: AnalyzeIn,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> StreamingResponse:
    source = db.get(Source, body.source_id)
    if source is None or not principal.can_see(source.tenant_id):
        raise HTTPException(status_code=404, detail=f"source {body.source_id} not found")
    return StreamingResponse(
        analyze(ctx, principal, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
