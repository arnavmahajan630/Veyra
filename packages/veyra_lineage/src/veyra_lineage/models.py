"""Result models of the lineage query library (IF-API-EVIDENCE, `/api/lineage`).

Every function in :mod:`veyra_lineage.queries` returns one of these, and the evidence API
(B4) serves them as-is. ``fixtures/*.json`` holds one example per model, so the console
(C5, B6) can build pages before the index holds real data.

Conventions:
- Event timestamps are RFC3339 strings with nanoseconds (``2026-09-26T08:35:11.123456789Z``),
  the same format as the Kafka records, so an event's times match its envelope exactly.
- Aggregate timestamps (minutes, last-seen) are timezone-aware UTC ``datetime`` values.
- Nullable contract fields (``contract_ref``, ``template_id``, ...) are ``None``, never ``''``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from veyra_common.models import Conformance, RawRef

MatchKind = Literal["event_uid", "sha256_prefix", "search_terms", "template_sig"]
RouteHealth = Literal["ok", "degraded", "failing", "idle"]
SourceStatus = Literal["ok", "low", "silent", "unexpected"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TierTotals(_Model):
    tier1: int = 0
    tier2: int = 0
    tier3: int = 0
    tier4: int = 0

    @property
    def total(self) -> int:
        return self.tier1 + self.tier2 + self.tier3 + self.tier4


# ---------------------------------------------------------------- overview
class SourceStrip(_Model):
    """One source in the overview strip (last 15 minutes)."""

    tenant_id: str
    source_id: str
    eps_1m: float
    raw_15m: int
    bytes_15m: int
    tiers: TierTotals
    last_seen: datetime | None = None


class RouteStatus(_Model):
    route_id: str
    delivered: int
    failed: int
    filtered: int
    last_at: datetime | None = None
    status: RouteHealth


class WindowRootInfo(_Model):
    window_id: str
    window_end: datetime
    leaf_count: int
    root: str
    immudb_verified: bool


class VaultStatus(_Model):
    segments: int
    last_sealed_at: datetime | None = None
    last_root: WindowRootInfo | None = None


class Overview(_Model):
    """``GET /lineage/overview`` — the Overview page's numbers."""

    tenant: str | None
    generated_at: datetime
    eps_1m: float
    totals_by_tier: TierTotals
    sources: list[SourceStrip]
    routes: list[RouteStatus]
    vault: VaultStatus


# ---------------------------------------------------------------- source health
class SourceHealth(_Model):
    """``GET /lineage/sources`` — one Source Health row."""

    tenant_id: str
    source_id: str
    expected_eps: float | None = None
    actual_eps: float
    last_seen: datetime | None = None
    tier_mix: TierTotals
    contract_ref: str | None = None
    clock_skew_p50_ms: float | None = None
    status: SourceStatus


# ---------------------------------------------------------------- search
class SearchHit(_Model):
    event_uid: str
    tenant_id: str
    source_id: str
    received_time: str | None = None
    revision: int | None = None
    tier: int | None = None
    conformance: Conformance | None = None
    template_sig: str | None = None
    contract_ref: str | None = None
    raw_sha256: str
    raw_preview: str | None = None


class SearchResult(_Model):
    """``GET /lineage/search`` — hits are the latest revision of each event, newest first."""

    q: str
    matched_on: MatchKind | None
    hits: list[SearchHit]


# ---------------------------------------------------------------- event detail
class RawInfo(_Model):
    """The envelope's metadata as indexed. The bytes themselves come from the vault or
    Kafka via ``raw_ref`` (B4); the index never stores them."""

    raw_ref: RawRef
    raw_sha256: str
    raw_len: int
    received_time: str
    tenant_id: str
    source_id: str
    vendor: str
    zone: str
    collector_id: str
    transport: str
    listener: str | None = None
    peer_ip: str | None = None
    custody: str
    auth_method: str
    framing_method: str
    framing_truncated: bool
    framing_parts: int
    raw_preview: str


class Revision(_Model):
    """One normalization of the event: the IF-LINEAGE row plus the IF-NORM-EVENT body."""

    revision: int
    tier: int
    conformance: Conformance
    contract_ref: str | None = None
    template_sig: str
    template_id: str | None = None
    class_uid: int
    category: str
    norm_topic: str
    produced_at: str
    replay: bool
    replay_job_id: str | None = None
    search_terms: list[str] = Field(default_factory=list)
    ocsf: dict[str, Any] | None = None  # None until norm.<category> is indexed


class VaultLocation(_Model):
    segment_id: str
    record_idx: int
    chain_hash: str
    sealed: bool
    sealed_at: str
    window_id: str | None = None


class ReceiptRow(_Model):
    revision: int
    route_id: str
    status: Literal["delivered", "filtered", "failed"]
    detail: str
    at: str


class DlqSample(_Model):
    event_uid: str
    tenant_id: str
    source_id: str
    tier: int
    reason_code: str
    reason_detail: str
    contract_ref: str | None = None
    template_sig: str
    text_masked: str
    parse_path: list[str]
    produced_at: str


class ShadowRow(_Model):
    contract_id: str
    active_ref: str | None = None
    candidate_ref: str
    active_tier: int
    candidate_tier: int
    changed_fields: list[str]
    regressions: list[str]
    produced_at: str


class EventDetail(_Model):
    """``GET /lineage/events/{event_uid}`` — everything the index knows about one event."""

    event_uid: str
    raw_ref: RawRef | None = None
    raw: RawInfo | None = None
    revisions: list[Revision]
    vault: VaultLocation | None = None
    receipts: list[ReceiptRow]
    dlq: list[DlqSample]
    shadow: list[ShadowRow]


# ---------------------------------------------------------------- templates / dlq
class TemplateEvent(_Model):
    """``GET /lineage/templates/{sig}/events`` — what replay and backtests need."""

    event_uid: str
    tenant_id: str
    source_id: str
    revision: int
    tier: int
    raw_ref: RawRef
    raw_sha256: str
    produced_at: str


# ---------------------------------------------------------------- shadow
class TierTransition(_Model):
    active_tier: int
    candidate_tier: int
    count: int


class FieldCount(_Model):
    field: str
    count: int


class ShadowSummary(_Model):
    """Candidate vs active over a window, per candidate version (A5 canary evidence)."""

    contract_id: str
    candidate_ref: str
    active_ref: str | None = None
    since: datetime
    total: int
    events: int
    improved: int
    unchanged: int
    worsened: int
    with_regressions: int
    first_at: datetime | None = None
    last_at: datetime | None = None
    transitions: list[TierTransition]
    top_changed_fields: list[FieldCount]
    top_regressions: list[FieldCount]
    regression_samples: list[str]


# ---------------------------------------------------------------- routes
class RouteMinute(_Model):
    minute: datetime
    delivered: int
    failed: int
    filtered: int


class RouteStats(_Model):
    route_id: str
    since: datetime
    delivered: int
    failed: int
    filtered: int
    last_at: datetime | None = None
    status: RouteHealth
    per_minute: list[RouteMinute]
