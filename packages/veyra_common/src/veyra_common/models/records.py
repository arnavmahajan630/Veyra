"""The remaining Kafka record types: IF-LINEAGE, IF-DLQ, IF-SHADOW, IF-VAULT-INDEX,
IF-RECEIPT, IF-AUDIT.

Each one is produced by exactly one service and consumed by at least two, so these
models are the contract test surface: fixtures live in packages/veyra_common/fixtures/.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from veyra_common.models.envelope import Sha256Hex
from veyra_common.models.norm import Category, Conformance, RawRef

ReasonCode = Literal[
    "no_contract",
    "no_template_match",
    "required_missing",
    "schema_invalid",
    "decode_error",
    "budget_exceeded",
    "size_exceeded",
    "engine_crash",
]
ReceiptStatus = Literal["delivered", "filtered", "failed"]


class LineageRecord(BaseModel):
    """IF-LINEAGE — one row per (event, revision), the index of everything."""

    model_config = ConfigDict(extra="forbid")

    event_uid: str
    revision: int = Field(ge=1)
    tenant_id: str
    source_id: str
    raw_ref: RawRef
    raw_sha256: Sha256Hex
    contract_ref: str | None = None  # "authsrv@3"
    template_sig: str
    template_id: str | None = None
    tier: int = Field(ge=1, le=4)
    conformance: Conformance
    class_uid: int
    category: Category
    norm_topic: str
    produced_at: str
    replay: bool = False
    replay_job_id: str | None = None
    # IPs, users and hostnames, for lineage search. Capped at 16 by the producer.
    search_terms: list[str] = Field(default_factory=list, max_length=16)


class DlqRecord(BaseModel):
    """IF-DLQ — emitted for every tier >= 2 event. Never a drop (P2), a copy."""

    model_config = ConfigDict(extra="forbid")

    event_uid: str
    tenant_id: str
    source_id: str
    tier: int = Field(ge=2, le=4)
    reason_code: ReasonCode
    reason_detail: str = ""
    contract_ref: str | None = None
    template_sig: str
    # PII-masked template text, <= 2 KB (veyra_engine.mask)
    text_masked: Annotated[str, Field(max_length=2048)] = ""
    parse_path: list[str] = Field(default_factory=list)
    produced_at: str


class ShadowRecord(BaseModel):
    """IF-SHADOW — candidate vs active comparison for one event (A5)."""

    model_config = ConfigDict(extra="forbid")

    event_uid: str
    contract_id: str
    active_ref: str | None = None
    candidate_ref: str
    active_tier: int = Field(ge=1, le=4)
    candidate_tier: int = Field(ge=1, le=4)
    changed_fields: list[str] = Field(default_factory=list)
    regressions: list[str] = Field(default_factory=list)
    produced_at: str


class VaultIndexEvent(BaseModel):
    """IF-VAULT-INDEX, per-event record — emitted at seal, inside the archiver's txn."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["event"] = "event"
    event_uid: str
    raw_topic: str
    partition: int
    offset: int
    segment_id: str
    record_idx: int
    chain_hash_hex: Sha256Hex
    sealed_at: str


class VaultIndexSegment(BaseModel):
    """IF-VAULT-INDEX, per-segment record."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["segment"] = "segment"
    segment_id: str
    raw_topic: str
    partition: int
    first_offset: int
    last_offset: int
    record_count: int
    prev_chain_hash_hex: Sha256Hex
    last_chain_hash_hex: Sha256Hex
    segment_digest_hex: Sha256Hex
    sealed_at: str


VaultIndexRecord = Annotated[
    VaultIndexEvent | VaultIndexSegment,
    Field(discriminator="kind"),
]


class Receipt(BaseModel):
    """IF-RECEIPT — delivery outcome per (event, route)."""

    model_config = ConfigDict(extra="forbid")

    event_uid: str
    revision: int = Field(ge=1)
    route_id: str
    status: ReceiptStatus
    detail: str = ""
    at: str


class AuditRecord(BaseModel):
    """IF-AUDIT — every human action in the control plane and evidence API."""

    model_config = ConfigDict(extra="forbid")

    actor: str
    role: str
    action: str
    target: str
    detail: str = ""
    at: str


class SignedRoot(BaseModel):
    """IF-SIGNED-ROOT payload (canonical JSON: sorted keys, no whitespace)."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    window_id: str
    window_start: int
    window_end: int
    leaf_count: int
    root: Sha256Hex
    segments: list[str] = Field(default_factory=list)
    prev_signed_sha256: Sha256Hex
    key_id: str
    alg: Literal["Ed25519"] = "Ed25519"
