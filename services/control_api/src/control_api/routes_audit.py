"""GET /audit (IF-API-CONTROL): newest first, scoped to the caller's tenant."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session as DbSession

from control_api.auth import Principal, current_principal
from control_api.context import AppContext, get_ctx, get_db
from control_api.tables import AuditRow

router = APIRouter(tags=["audit"])


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor: str
    role: str
    action: str
    target: str
    detail: str
    at: str


@router.get("/audit", response_model=list[AuditOut])
def get_audit(
    limit: int | None = Query(default=None, ge=1),
    principal: Principal = Depends(current_principal),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> list[AuditRow]:
    size = min(limit or ctx.cfg.api_page_default, ctx.cfg.api_page_max)
    tenant = None if principal.is_platform else principal.tenant_id
    return ctx.audit.list(db, tenant_id=tenant, limit=size)
