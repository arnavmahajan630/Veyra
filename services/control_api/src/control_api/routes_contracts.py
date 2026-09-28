"""Contracts (IF-API-CONTROL): list, detail, submit, approve, promote, rollback.

Every change: rows + transitions + audit → publish the changed IF-CONTROL keys (flushed)
→ commit → SSE ``contract`` with ``{id, version, state, action}``.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal, current_principal, require
from control_api.context import AppContext, get_ctx, get_db, publish_then_commit
from control_api.diff import semantic_diff, yaml_diff
from control_api.drift import drift_event, resolve_covered
from control_api.registry import (
    ACTORS,
    APPROVERS,
    WRITERS,
    RegistryError,
    approve,
    backtest,
    compiled_of,
    get_version,
    promote,
    rollback,
    submit,
    versions_of,
    visible_contract,
)
from control_api.tables import Contract, ContractTransition, ContractVersion

router = APIRouter(tags=["contracts"])


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    contract_id: str
    version: int
    state: str
    author: str
    approved_by: str | None
    approved_at: str | None
    promoted_by: str | None
    promoted_at: str | None
    retired_reason: str | None
    git_commit: str | None
    created_at: str
    draft_id: str | None


class VersionDetail(VersionSummary):
    yaml: str
    compiled: dict[str, Any] | None
    golden: dict[str, Any] | None
    lint: list[dict[str, Any]]
    backtest: dict[str, Any] | None


class TransitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    version: int
    action: str
    from_state: str | None
    to_state: str
    actor: str
    at: str
    reason: str


class ContractSummary(BaseModel):
    id: str
    tenant_id: str
    sources: list[str]
    active_version: int | None
    canary_version: int | None
    latest_version: int
    latest_state: str
    updated_at: str | None
    updated_by: str | None


class ContractDetail(ContractSummary):
    versions: list[VersionSummary]
    history: list[TransitionOut]


class SubmitIn(BaseModel):
    yaml: str = Field(min_length=1)
    draft_id: str | None = None


class RollbackIn(BaseModel):
    to_version: int = Field(ge=1)


class DiffOut(BaseModel):
    contract_id: str
    from_version: int
    to_version: int
    semantic: dict[str, Any]
    yaml: str


class BacktestIn(BaseModel):
    template_sigs: list[str] = Field(default_factory=list)
    samples: list[str] = Field(default_factory=list)


def _raise(exc: RegistryError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.detail)


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def version_detail(row: ContractVersion) -> VersionDetail:
    return VersionDetail(
        **VersionSummary.model_validate(row).model_dump(),
        yaml=row.yaml,
        compiled=compiled_of(row) if row.compiled_json else None,
        golden=_loads(row.golden_report_json),
        lint=_loads(row.lint_json) or [],
        backtest=_loads(row.backtest_json),
    )


def _history(db: DbSession, contract_id: str) -> list[ContractTransition]:
    query = select(ContractTransition).where(col(ContractTransition.contract_id) == contract_id)
    return list(db.exec(query.order_by(col(ContractTransition.id))).all())


def _summary(db: DbSession, contract: Contract) -> ContractSummary:
    versions = versions_of(db, contract.id)
    latest = versions[-1]
    history = _history(db, contract.id)
    return ContractSummary(
        id=contract.id,
        tenant_id=contract.tenant_id,
        sources=compiled_of(latest).get("sources", []),
        active_version=contract.active_version,
        canary_version=contract.canary_version,
        latest_version=latest.version,
        latest_state=latest.state,
        updated_at=history[-1].at if history else latest.created_at,
        updated_by=history[-1].actor if history else latest.author,
    )


def _announce(ctx: AppContext, row: ContractVersion, action: str) -> None:
    ctx.hub.publish(
        "contract",
        {"id": row.contract_id, "version": row.version, "state": row.state, "action": action},
    )


@router.get("/contracts", response_model=list[ContractSummary])
def list_contracts(
    tenant: str | None = None,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> list[ContractSummary]:
    query = select(Contract).order_by(col(Contract.id))
    if tenant is not None:
        query = query.where(col(Contract.tenant_id) == tenant)
    return [
        _summary(db, c)
        for c in db.exec(query).all()
        if principal.can_see(c.tenant_id) and versions_of(db, c.id)
    ]


@router.post("/contracts", response_model=VersionDetail, status_code=201)
def submit_contract(
    body: SubmitIn,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    try:
        result = submit(ctx, db, principal, body.yaml, draft_id=body.draft_id)
    except RegistryError as exc:
        db.rollback()
        raise _raise(exc) from exc
    publish_then_commit(ctx, db, result.items)
    row = result.version
    db.refresh(row)
    if row.state == "canary":  # C2: the backtest runs as soon as a version reaches canary
        contract = db.get(Contract, row.contract_id)
        assert contract is not None
        backtest(ctx, db, contract, row)
        db.commit()
        db.refresh(row)
    _announce(ctx, row, "submitted")
    return version_detail(row)


@router.get("/contracts/{contract_id}", response_model=ContractDetail)
def get_contract(
    contract_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> ContractDetail:
    try:
        contract = visible_contract(db, principal, contract_id)
    except RegistryError as exc:
        raise _raise(exc) from exc
    return ContractDetail(
        **_summary(db, contract).model_dump(),
        versions=[VersionSummary.model_validate(v) for v in versions_of(db, contract_id)],
        history=[TransitionOut.model_validate(t) for t in _history(db, contract_id)],
    )


@router.get("/contracts/{contract_id}/versions/{version}", response_model=VersionDetail)
def get_contract_version(
    contract_id: str,
    version: int,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    try:
        visible_contract(db, principal, contract_id)
        return version_detail(get_version(db, contract_id, version))
    except RegistryError as exc:
        raise _raise(exc) from exc


@router.post("/contracts/{contract_id}/versions/{version}/approve", response_model=VersionDetail)
def approve_version(
    contract_id: str,
    version: int,
    principal: Principal = Depends(require(*APPROVERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    try:
        row = approve(ctx, db, principal, contract_id, version)
    except RegistryError as exc:
        db.rollback()
        raise _raise(exc) from exc
    db.commit()
    db.refresh(row)
    _announce(ctx, row, "approved")
    return version_detail(row)


@router.post("/contracts/{contract_id}/versions/{version}/promote", response_model=VersionDetail)
def promote_version(
    contract_id: str,
    version: int,
    principal: Principal = Depends(require(*APPROVERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    try:
        row, items = promote(ctx, db, principal, contract_id, version)
    except RegistryError as exc:
        db.rollback()
        raise _raise(exc) from exc
    resolved = resolve_covered(ctx, db, contract_id)  # C3 AC2
    publish_then_commit(ctx, db, items)
    db.refresh(row)
    _announce(ctx, row, "promoted")
    for item in resolved:
        ctx.hub.publish("drift", drift_event(item))
    return version_detail(row)


@router.post("/contracts/{contract_id}/rollback", response_model=VersionDetail)
def rollback_contract(
    contract_id: str,
    body: RollbackIn,
    principal: Principal = Depends(require(*APPROVERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> VersionDetail:
    try:
        row, items = rollback(ctx, db, principal, contract_id, body.to_version)
    except RegistryError as exc:
        db.rollback()
        raise _raise(exc) from exc
    resolved = resolve_covered(ctx, db, contract_id)
    publish_then_commit(ctx, db, items)
    db.refresh(row)
    _announce(ctx, row, "rolled_back")
    for item in resolved:
        ctx.hub.publish("drift", drift_event(item))
    return version_detail(row)


@router.post("/contracts/{contract_id}/versions/{version}/backtest")
def backtest_version(
    contract_id: str,
    version: int,
    body: BacktestIn | None = None,
    principal: Principal = Depends(require(*ACTORS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        contract = visible_contract(db, principal, contract_id)
        row = get_version(db, contract_id, version)
    except RegistryError as exc:
        raise _raise(exc) from exc
    request = body or BacktestIn()
    result = backtest(ctx, db, contract, row, sigs=request.template_sigs, samples=request.samples)
    db.commit()
    return result


@router.get("/contracts/{contract_id}/diff", response_model=DiffOut)
def diff_versions(
    contract_id: str,
    from_version: int | None = Query(default=None, alias="from"),
    to_version: int | None = Query(default=None, alias="to"),
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> DiffOut:
    """``to`` defaults to the latest version and ``from`` to the one before it."""
    try:
        visible_contract(db, principal, contract_id)
        versions = versions_of(db, contract_id)
        to_n = to_version if to_version is not None else versions[-1].version
        from_n = from_version if from_version is not None else to_n - 1
        old, new = get_version(db, contract_id, from_n), get_version(db, contract_id, to_n)
    except RegistryError as exc:
        raise _raise(exc) from exc
    return DiffOut(
        contract_id=contract_id,
        from_version=from_n,
        to_version=to_n,
        semantic=semantic_diff(compiled_of(old), compiled_of(new)),
        yaml=yaml_diff(old.yaml, new.yaml, f"{contract_id}@{from_n}", f"{contract_id}@{to_n}"),
    )
