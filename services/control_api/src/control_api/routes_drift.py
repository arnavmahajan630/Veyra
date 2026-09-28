"""The drift inbox (IF-API-CONTROL): ``GET /drift``, ``GET /drift/{id}``, dismiss.

``POST /drift/{id}/draft`` arrives with the drafter (C4, Plan 5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal, current_principal, require
from control_api.context import AppContext, get_ctx, get_db
from control_api.drift import DriftOut, drift_event, drift_out
from control_api.registry import ACTORS
from control_api.tables import DriftItem

router = APIRouter(tags=["drift"])


def load_item(db: DbSession, principal: Principal, drift_id: str) -> DriftItem:
    item = db.get(DriftItem, drift_id)
    if item is None or not principal.can_see(item.tenant_id):
        raise HTTPException(status_code=404, detail=f"drift item {drift_id} not found")
    return item


@router.get("/drift", response_model=list[DriftOut])
def list_drift(
    state: str | None = None,
    source_id: str | None = None,
    principal: Principal = Depends(current_principal),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> list[DriftOut]:
    query = select(DriftItem).order_by(col(DriftItem.last_seen).desc(), col(DriftItem.drift_id))
    if state is not None:
        query = query.where(col(DriftItem.state) == state)
    if source_id is not None:
        query = query.where(col(DriftItem.source_id) == source_id)
    items = db.exec(query.limit(ctx.cfg.api_page_default)).all()
    return [drift_out(i) for i in items if principal.can_see(i.tenant_id)]


@router.get("/drift/{drift_id}", response_model=DriftOut)
def get_drift(
    drift_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> DriftOut:
    return drift_out(load_item(db, principal, drift_id))


@router.post("/drift/{drift_id}/dismiss", response_model=DriftOut)
def dismiss_drift(
    drift_id: str,
    principal: Principal = Depends(require(*ACTORS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> DriftOut:
    item = load_item(db, principal, drift_id)
    item.state = "dismissed"
    item.updated_at = ctx.now()
    db.add(item)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="drift.dismiss",
        target=drift_id, detail=f"{item.source_id} {item.template_sig}",
        tenant_id=item.tenant_id,
    )  # fmt: skip
    db.commit()
    db.refresh(item)
    ctx.hub.publish("drift", drift_event(item))
    return drift_out(item)
