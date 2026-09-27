"""Tenants (IF-API-CONTROL). Tenants are not on the control topic; they are audited."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal, current_principal, require, visible_or_404
from control_api.context import AppContext, get_ctx, get_db
from control_api.tables import Tenant

router = APIRouter(prefix="/tenants", tags=["tenants"])


class TenantIn(BaseModel):
    id: str = Field(pattern=r"^t_[a-z0-9_]+$")
    name: str = Field(min_length=1)


class TenantPatch(BaseModel):
    name: str = Field(min_length=1)


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: str


@router.get("", response_model=list[TenantOut])
def list_tenants(
    principal: Principal = Depends(current_principal), db: DbSession = Depends(get_db)
) -> list[Tenant]:
    tenants = db.exec(select(Tenant).order_by(col(Tenant.id))).all()
    return [t for t in tenants if principal.can_see(t.id)]


@router.post("", response_model=TenantOut, status_code=201)
def create_tenant(
    body: TenantIn,
    principal: Principal = Depends(require("admin")),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Tenant:
    if db.get(Tenant, body.id) is not None:
        raise HTTPException(status_code=409, detail=f"tenant {body.id} already exists")
    tenant = Tenant(id=body.id, name=body.name, created_at=ctx.now())
    db.add(tenant)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="tenant.create", target=body.id,
        tenant_id=body.id,
    )  # fmt: skip
    db.commit()
    db.refresh(tenant)
    return tenant


@router.get("/{tenant_id}", response_model=TenantOut)
def get_tenant(
    tenant_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} not found")
    visible_or_404(principal, tenant.id, f"tenant {tenant_id}")
    return tenant


@router.patch("/{tenant_id}", response_model=TenantOut)
def patch_tenant(
    tenant_id: str,
    body: TenantPatch,
    principal: Principal = Depends(require("admin")),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail=f"tenant {tenant_id} not found")
    tenant.name = body.name
    db.add(tenant)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="tenant.update", target=tenant_id,
        detail=f"name={body.name}", tenant_id=tenant_id,
    )  # fmt: skip
    db.commit()
    db.refresh(tenant)
    return tenant
