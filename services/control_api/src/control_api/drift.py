"""The drift inbox (C3): upserts from the drift worker, and auto-resolution.

An item is keyed by ``(source_id, template_sig)``. The worker sends absolute counts, so an
upsert *sets* count, samples and last-seen; it never changes a human decision (a
``dismissed`` item stays dismissed). An item is **resolved** as soon as the source's
active contract has a template whose regex matches one of its samples — at upsert time,
and whenever a version is promoted or rolled back (decision TC24: active only, not canary).

States: ``open | drafting | draft_ready | resolved | dismissed``.
"""

from __future__ import annotations

import json
from typing import Any

import re2
from pydantic import BaseModel, Field
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.context import AppContext
from control_api.registry import compiled_of
from control_api.tables import Contract, ContractVersion, DriftItem, Source
from veyra_common.ids import uuid7

UNRESOLVED = ("open", "drafting", "draft_ready")


class DriftIn(BaseModel):
    """``POST /internal/drift``: IF-API-CONTROL plus ``related_sigs``, ``sample_event_uids``."""

    source_id: str
    template_sig: str
    drain_template: str = ""
    count: int = Field(ge=0)
    samples_masked: list[str] = Field(default_factory=list)
    sample_event_uids: list[str] = Field(default_factory=list)
    related_sigs: list[str] = Field(default_factory=list)
    first_seen: str
    last_seen: str


class DriftOut(BaseModel):
    drift_id: str
    source_id: str
    tenant_id: str
    template_sig: str
    related_sigs: list[str]
    drain_template: str
    count: int
    first_seen: str
    last_seen: str
    samples_masked: list[str]
    sample_event_uids: list[str]
    state: str
    draft_id: str | None
    resolved_by: str | None
    updated_at: str | None


def drift_out(item: DriftItem) -> DriftOut:
    return DriftOut(
        drift_id=item.drift_id,
        source_id=item.source_id,
        tenant_id=item.tenant_id,
        template_sig=item.template_sig,
        related_sigs=json.loads(item.related_sigs_json),
        drain_template=item.drain_template,
        count=item.count,
        first_seen=item.first_seen,
        last_seen=item.last_seen,
        samples_masked=json.loads(item.samples_masked_json),
        sample_event_uids=json.loads(item.sample_event_uids_json),
        state=item.state,
        draft_id=item.draft_id,
        resolved_by=item.resolved_by,
        updated_at=item.updated_at,
    )


def drift_event(item: DriftItem, *, created: bool = False) -> dict[str, Any]:
    """The SSE ``drift`` payload (Plan 6 toasts on ``data.source_id``)."""
    return {
        "drift_id": item.drift_id,
        "source_id": item.source_id,
        "template_sig": item.template_sig,
        "count": item.count,
        "state": item.state,
        "created": created,
    }


def _active_for_source(db: DbSession, source: Source) -> ContractVersion | None:
    if source.contract_id is None:
        return None
    contract = db.get(Contract, source.contract_id)
    if contract is None or contract.active_version is None:
        return None
    return db.get(ContractVersion, (contract.id, contract.active_version))


def covered_by(version: ContractVersion, samples: list[str]) -> bool:
    """Does any template of this version match any sample (the template text)?"""
    regexes = [re2.compile(t["regex"]) for t in compiled_of(version).get("templates", [])]
    return any(r.search(sample) for r in regexes for sample in samples)


def _resolve(ctx: AppContext, item: DriftItem, version: ContractVersion) -> None:
    item.state = "resolved"
    item.resolved_by = f"{version.contract_id}@{version.version}"
    item.updated_at = ctx.now()


def upsert(ctx: AppContext, db: DbSession, body: DriftIn) -> tuple[DriftItem, bool] | None:
    """Create or update the item; None when the source is unknown."""
    source = db.get(Source, body.source_id)
    if source is None:
        return None
    query = select(DriftItem).where(
        col(DriftItem.source_id) == body.source_id,
        col(DriftItem.template_sig) == body.template_sig,
    )
    item = db.exec(query).first()
    created = item is None
    if item is None:
        item = DriftItem(
            drift_id=f"dr_{uuid7().hex}",
            source_id=source.id,
            tenant_id=source.tenant_id,
            template_sig=body.template_sig,
            first_seen=body.first_seen,
            last_seen=body.last_seen,
        )
    item.count = body.count
    item.first_seen = min(item.first_seen, body.first_seen)
    item.last_seen = max(item.last_seen, body.last_seen)
    item.drain_template = body.drain_template
    item.related_sigs_json = json.dumps(body.related_sigs)
    item.samples_masked_json = json.dumps(body.samples_masked)
    item.sample_event_uids_json = json.dumps(body.sample_event_uids)
    item.updated_at = ctx.now()
    active = _active_for_source(db, source)
    if item.state in UNRESOLVED and active is not None and covered_by(active, body.samples_masked):
        _resolve(ctx, item, active)
    db.add(item)
    return item, created


def resolve_covered(ctx: AppContext, db: DbSession, contract_id: str) -> list[DriftItem]:
    """After a promote or rollback: resolve the open items the active version now covers."""
    contract = db.get(Contract, contract_id)
    if contract is None or contract.active_version is None:
        return []
    active = db.get(ContractVersion, (contract.id, contract.active_version))
    if active is None:
        return []
    sources = compiled_of(active).get("sources", [])
    query = select(DriftItem).where(
        col(DriftItem.source_id).in_(sources), col(DriftItem.state).in_(UNRESOLVED)
    )
    resolved = []
    for item in db.exec(query).all():
        if covered_by(active, json.loads(item.samples_masked_json)):
            _resolve(ctx, item, active)
            db.add(item)
            resolved.append(item)
    return resolved
