"""Replay jobs (IF-API-CONTROL): ``POST /replay``, ``GET /replay/{job_id}``, ``GET /replay``."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal, current_principal, require
from control_api.backtest import OPEN_DRIFT, drift_sigs
from control_api.context import AppContext, get_ctx, get_db
from control_api.registry import ACTORS, RegistryError, compiled_of, get_version, visible_contract
from control_api.replay import job_event, run_job
from control_api.tables import ReplayJob
from veyra_common.ids import uuid7

router = APIRouter(tags=["replay"])

# A replay usually follows a promote that has just resolved the drift item it was for.
REPLAYABLE_DRIFT = (*OPEN_DRIFT, "resolved")


class ReplayIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    contract_id: str
    template_sigs: list[str] = Field(default_factory=list)
    source_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class ReplayOut(BaseModel):
    job_id: str
    contract_id: str
    tenant_id: str
    state: str
    total: int
    published: int
    normalized: int
    params: dict[str, Any]
    created_by: str | None
    created_at: str
    finished_at: str | None
    detail: str


def _out(job: ReplayJob) -> ReplayOut:
    return ReplayOut(
        job_id=job.job_id,
        contract_id=job.contract_id,
        tenant_id=job.tenant_id,
        state=job.state,
        total=job.total,
        published=job.published,
        normalized=job.normalized,
        params=json.loads(job.params_json),
        created_by=job.created_by,
        created_at=job.created_at,
        finished_at=job.finished_at,
        detail=job.detail,
    )


@router.post("/replay", response_model=ReplayOut, status_code=202)
def create_replay(
    body: ReplayIn,
    principal: Principal = Depends(require(*ACTORS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> ReplayOut:
    try:
        contract = visible_contract(db, principal, body.contract_id)
    except RegistryError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    if contract.active_version is None:
        raise HTTPException(status_code=409, detail=f"{contract.id} has no active version yet")
    sources = compiled_of(get_version(db, contract.id, contract.active_version))["sources"]
    sigs = body.template_sigs or drift_sigs(db, sources, REPLAYABLE_DRIFT)
    if not sigs:
        raise HTTPException(status_code=422, detail="no template sigs to replay")
    params = {"template_sigs": sigs, "source_id": body.source_id, "from": body.from_, "to": body.to}
    job = ReplayJob(
        job_id=f"rj_{uuid7().hex}",
        contract_id=contract.id,
        tenant_id=contract.tenant_id,
        params_json=json.dumps(params),
        created_by=principal.email,
        created_at=ctx.now(),
    )
    db.add(job)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="replay.create",
        target=job.job_id, detail=f"contract={contract.id} sigs={','.join(sigs)}",
        tenant_id=contract.tenant_id,
    )  # fmt: skip
    db.commit()
    db.refresh(job)
    ctx.hub.publish("replay", job_event(job))
    ctx.spawn(lambda: run_job(ctx, job.job_id))
    db.refresh(job)
    return _out(job)


@router.get("/replay/{job_id}", response_model=ReplayOut)
def get_replay(
    job_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> ReplayOut:
    job = db.get(ReplayJob, job_id)
    if job is None or not principal.can_see(job.tenant_id):
        raise HTTPException(status_code=404, detail=f"replay job {job_id} not found")
    return _out(job)


@router.get("/replay", response_model=list[ReplayOut])
def list_replays(
    contract_id: str | None = Query(default=None),
    principal: Principal = Depends(current_principal),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> list[ReplayOut]:
    # job ids are UUIDv7, so they break created_at ties in creation order too
    query = select(ReplayJob).order_by(
        col(ReplayJob.created_at).desc(), col(ReplayJob.job_id).desc()
    )
    if contract_id is not None:
        query = query.where(col(ReplayJob.contract_id) == contract_id)
    jobs = db.exec(query.limit(ctx.cfg.api_page_default)).all()
    return [_out(j) for j in jobs if principal.can_see(j.tenant_id)]
