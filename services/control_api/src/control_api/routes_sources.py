"""Sources and API keys (IF-API-CONTROL).

Every mutation: rows + audit → publish the changed IF-CONTROL key (flushed) → commit.
A publish failure rolls back and returns 503, so SQLite never gets ahead of `control`.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal, current_principal, require
from control_api.context import AppContext, get_ctx, get_db, publish_then_commit
from control_api.inventory import rewrite_inventory
from control_api.keys import IssuedKey, key_card, secret_digest
from control_api.messages import apikey_message, source_message
from control_api.tables import ApiKey, Source, Tenant
from veyra_common.ids import new_api_key_id, new_api_key_secret
from veyra_common.models import Zone, control_key

SourceTransport = Literal["syslog_udp", "syslog_tcp", "http_push"]
MatchKind = Literal["peer_ip", "syslog_host"]
SYSLOG = frozenset({"syslog_udp", "syslog_tcp"})
WRITERS = ("admin", "pack_author")

router = APIRouter(tags=["sources"])


class SourceIn(BaseModel):
    id: str = Field(pattern=r"^src_[a-z0-9_]+$")
    tenant_id: str
    name: str = Field(min_length=1)
    vendor: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    zone: Zone
    transport: SourceTransport
    listener: str | None = None
    match_kind: MatchKind | None = None
    match_value: str | None = None
    contract_id: str | None = None
    expected_eps: float = Field(default=0.0, ge=0)
    salt_buckets: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _syslog_sources_are_resolvable(self) -> SourceIn:
        if self.transport in SYSLOG and not (
            self.listener and self.match_kind and self.match_value
        ):
            raise ValueError("syslog sources need listener, match_kind and match_value")
        return self


class SourcePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    contract_id: str | None = None
    expected_eps: float | None = Field(default=None, ge=0)
    salt_buckets: int | None = Field(default=None, ge=1)
    status: Literal["active", "paused"] | None = None
    listener: str | None = None
    match_kind: MatchKind | None = None
    match_value: str | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    name: str
    vendor: str
    zone: str
    transport: str
    listener: str | None
    match_kind: str | None
    match_value: str | None
    contract_id: str | None
    expected_eps: float
    salt_buckets: int
    status: str
    created_at: str


class KeyIn(BaseModel):
    quota_eps: int | None = Field(default=None, ge=1)


class SyslogTarget(BaseModel):
    host: str
    port: int | None
    listener: str | None


class Endpoints(BaseModel):
    hec_url: str
    batch_url: str
    syslog: SyslogTarget | None


class KeyCard(BaseModel):
    key_id: str
    secret: str
    endpoints: Endpoints
    curl_example: str


class KeyRevoked(BaseModel):
    key_id: str
    status: str


class KeyRow(BaseModel):
    """A key as the Sources drawer lists it: never the secret, never its digest."""

    model_config = ConfigDict(from_attributes=True)

    key_id: str
    source_id: str
    status: str
    quota_eps: int
    created_by: str
    created_at: str
    revoked_at: str | None


def _load_source(db: DbSession, principal: Principal, source_id: str) -> Source:
    source = db.get(Source, source_id)
    if source is None or not principal.can_see(source.tenant_id):
        raise HTTPException(status_code=404, detail=f"source {source_id} not found")
    return source


def _source_changed(ctx: AppContext, db: DbSession, source: Source) -> None:
    rewrite_inventory(db, ctx.cfg)
    ctx.hub.publish("source", SourceOut.model_validate(source).model_dump())


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    tenant: str | None = None,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> list[Source]:
    query = select(Source).order_by(col(Source.id))
    if tenant is not None:
        query = query.where(col(Source.tenant_id) == tenant)
    return [s for s in db.exec(query).all() if principal.can_see(s.tenant_id)]


@router.post("/sources", response_model=SourceOut, status_code=201)
def create_source(
    body: SourceIn,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Source:
    if db.get(Tenant, body.tenant_id) is None or not principal.can_see(body.tenant_id):
        raise HTTPException(status_code=404, detail=f"tenant {body.tenant_id} not found")
    if db.get(Source, body.id) is not None:
        raise HTTPException(status_code=409, detail=f"source {body.id} already exists")
    source = Source.model_validate({**body.model_dump(), "created_at": ctx.now()})
    db.add(source)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="source.create", target=source.id,
        tenant_id=source.tenant_id,
    )  # fmt: skip
    publish_then_commit(ctx, db, [(control_key("source", source.id), source_message(source))])
    _source_changed(ctx, db, source)
    return source


@router.get("/sources/{source_id}", response_model=SourceOut)
def get_source(
    source_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> Source:
    return _load_source(db, principal, source_id)


@router.patch("/sources/{source_id}", response_model=SourceOut)
def patch_source(
    source_id: str,
    body: SourcePatch,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Source:
    source = _load_source(db, principal, source_id)
    changes = body.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(source, field, value)
    db.add(source)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="source.update", target=source_id,
        detail=",".join(sorted(changes)), tenant_id=source.tenant_id,
    )  # fmt: skip
    publish_then_commit(ctx, db, [(control_key("source", source.id), source_message(source))])
    _source_changed(ctx, db, source)
    return source


@router.get("/sources/{source_id}/keys", response_model=list[KeyRow])
def list_keys(
    source_id: str,
    principal: Principal = Depends(current_principal),
    db: DbSession = Depends(get_db),
) -> list[ApiKey]:
    source = _load_source(db, principal, source_id)
    query = select(ApiKey).where(col(ApiKey.source_id) == source.id)
    return list(db.exec(query.order_by(col(ApiKey.created_at), col(ApiKey.key_id))).all())


@router.post("/sources/{source_id}/keys", response_model=KeyCard, status_code=201)
def issue_key(
    source_id: str,
    body: KeyIn | None = None,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> KeyCard:
    source = _load_source(db, principal, source_id)
    key_id, secret = new_api_key_id(), new_api_key_secret()
    quota = body.quota_eps if body is not None and body.quota_eps else None
    key = ApiKey(
        key_id=key_id,
        source_id=source.id,
        tenant_id=source.tenant_id,
        secret_sha256=secret_digest(ctx.pepper, secret),
        pepper_id=ctx.pepper_id,
        quota_eps=quota or ctx.cfg.gateway_default_quota_eps,
        created_by=principal.email,
        created_at=ctx.now(),
    )
    db.add(key)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="key.create", target=key_id,
        detail=f"source={source.id}", tenant_id=source.tenant_id,
    )  # fmt: skip
    card = KeyCard.model_validate(
        key_card(public_host=ctx.cfg.public_host, key_id=key_id, secret=secret, source=source)
    )
    publish_then_commit(ctx, db, [(control_key("apikey", key_id), apikey_message(key))])
    if ctx.cfg.demo_mode:
        ctx.last_key.remember(IssuedKey(key_id=key_id, secret=secret, source_id=source_id))
    ctx.hub.publish("source", {"source_id": source_id, "key_id": key_id, "event": "key_issued"})
    return card


@router.post("/keys/{key_id}/revoke", response_model=KeyRevoked)
def revoke_key(
    key_id: str,
    principal: Principal = Depends(require(*WRITERS)),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> KeyRevoked:
    key = db.get(ApiKey, key_id)
    if key is None or not principal.can_see(key.tenant_id):
        raise HTTPException(status_code=404, detail=f"key {key_id} not found")
    key.status = "revoked"
    key.revoked_at = ctx.now()
    db.add(key)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="key.revoke", target=key_id,
        tenant_id=key.tenant_id,
    )  # fmt: skip
    publish_then_commit(ctx, db, [(control_key("apikey", key_id), apikey_message(key))])
    ctx.hub.publish(
        "source", {"source_id": key.source_id, "key_id": key_id, "event": "key_revoked"}
    )
    return KeyRevoked(key_id=key_id, status="revoked")
