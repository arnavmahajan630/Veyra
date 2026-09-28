"""Table rows → IF-CONTROL messages, and the full republish that keeps ``control`` complete."""

from __future__ import annotations

import json

from pydantic import BaseModel
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.publisher import ControlPublisher
from control_api.seed_docs import SeedDocs
from control_api.tables import ApiKey, Contract, ContractVersion, Source
from veyra_common.models import ApiKeyMessage, ContractMessage, SourceMessage, control_key

# C1 stores the operator's vocabulary; IF-ENVELOPE's Transport is what consumers read.
WIRE_TRANSPORT = {
    "syslog_udp": "syslog_udp",
    "syslog_tcp": "syslog_tcp",
    "http_push": "http_hec_event",
}


def source_message(src: Source) -> SourceMessage:
    return SourceMessage.model_validate(
        {
            "source_id": src.id,
            "tenant_id": src.tenant_id,
            "vendor": src.vendor,
            "zone": src.zone,
            "transport": WIRE_TRANSPORT[src.transport],
            "contract_id": src.contract_id,
            "expected_eps": src.expected_eps,
            "salt_buckets": src.salt_buckets,
            "status": src.status,
        }
    )


def apikey_message(key: ApiKey) -> ApiKeyMessage:
    return ApiKeyMessage.model_validate(
        {
            "key_id": key.key_id,
            "secret_sha256": key.secret_sha256,
            "pepper_id": key.pepper_id,
            "source_id": key.source_id,
            "tenant_id": key.tenant_id,
            "status": key.status,
            "quota_eps": key.quota_eps,
            "created_at": key.created_at,
        }
    )


def contract_message(
    active: ContractVersion, candidate: ContractVersion | None, published_at: str
) -> ContractMessage:
    compiled = json.loads(active.compiled_json or "{}")
    payload = {
        "id": active.contract_id,
        "version": active.version,
        "state": active.state,
        "tenant_id": compiled.get("tenant", ""),
        "sources": compiled.get("sources", []),
        "compiled": compiled,
        "candidate": None,
        "published_at": published_at,
    }
    if candidate is not None:
        payload["candidate"] = {
            "version": candidate.version,
            "compiled": json.loads(candidate.compiled_json or "{}"),
        }
    return ContractMessage.model_validate(payload)


def contract_items(
    db: DbSession, contract: Contract, published_at: str
) -> list[tuple[str, BaseModel]]:
    """The ``contract:<id>`` message for a contract, or nothing.

    A contract with no active version publishes nothing, even while it has a canary:
    IF-CONTROL's ``compiled`` (the active version) is required. Its first version reaches
    the data plane when it is promoted (decision TC16).
    """
    if contract.active_version is None:
        return []
    active = db.get(ContractVersion, (contract.id, contract.active_version))
    if active is None:
        return []
    candidate = (
        db.get(ContractVersion, (contract.id, contract.canary_version))
        if contract.canary_version is not None
        else None
    )
    message = contract_message(active, candidate, published_at)
    return [(control_key("contract", contract.id), message)]


def control_messages(
    db: DbSession, docs: SeedDocs, published_at: str
) -> list[tuple[str, BaseModel]]:
    """Every key the compacted topic must hold: sources, active keys, contracts, docs."""
    out: list[tuple[str, BaseModel]] = []
    for src in db.exec(select(Source).order_by(col(Source.id))).all():
        out.append((control_key("source", src.id), source_message(src)))
    active_keys = select(ApiKey).where(col(ApiKey.status) == "active").order_by(col(ApiKey.key_id))
    for key in db.exec(active_keys).all():
        out.append((control_key("apikey", key.key_id), apikey_message(key)))
    for contract in db.exec(select(Contract).order_by(col(Contract.id))).all():
        out.extend(contract_items(db, contract, published_at))
    out.extend((control_key("vocab", v.name), v) for v in docs.vocab)
    out.extend((control_key("enrich", e.name), e) for e in docs.enrich)
    out.append((control_key("routes"), docs.routes))
    return out


def published_keys(db: DbSession, docs: SeedDocs) -> set[str]:
    """Every key this database has put on ``control``, revoked keys included."""
    keys = {control_key("source", s.id) for s in db.exec(select(Source)).all()}
    keys |= {control_key("apikey", k.key_id) for k in db.exec(select(ApiKey)).all()}
    keys |= {
        control_key("contract", c.id)
        for c in db.exec(select(Contract)).all()
        if c.active_version is not None
    }
    keys |= {control_key("vocab", v.name) for v in docs.vocab}
    keys |= {control_key("enrich", e.name) for e in docs.enrich}
    return keys | {control_key("routes")}


def republish_all(
    db: DbSession,
    publisher: ControlPublisher,
    docs: SeedDocs,
    published_at: str,
    *,
    stale: set[str] | frozenset[str] = frozenset(),
) -> int:
    """C1: run at startup and after reset, so ``control`` is always complete.

    ``stale`` are keys an earlier state published; any this state no longer holds get a
    tombstone, so a reset really forgets the contracts, sources and keys made since seed.
    """
    items: list[tuple[str, BaseModel | None]] = list(control_messages(db, docs, published_at))
    current = {key for key, _ in items}
    items.extend((key, None) for key in sorted(stale - current))
    return publisher.publish_many(items)
