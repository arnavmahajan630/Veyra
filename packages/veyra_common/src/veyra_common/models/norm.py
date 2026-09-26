"""IF-ULPF and IF-NORM-EVENT — the normalized event and its lineage extension.

``ulpf`` is strictly typed because every consumer (router, indexer, console, verify)
depends on it. The OCSF body stays a loose dict: the engine validates it against the
vendored OCSF subset schemas (IF-OCSF-SUBSET), not against Pydantic.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from veyra_common.models.envelope import Custody, Sha256Hex, Zone

Conformance = Literal["match", "partial", "unknown_template", "unparseable"]
Confidence = Literal["low", "medium", "high"]
Category = Literal[
    "system", "findings", "iam", "network", "discovery", "application", "uncategorized"
]


class RawRef(BaseModel):
    """Where the raw envelope sits in Kafka."""

    model_config = ConfigDict(extra="forbid")

    topic: str
    partition: int
    offset: int


class ContractRef(BaseModel):
    """The contract that produced this event, if any."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int

    def __str__(self) -> str:  # "authsrv@3" (IF-NAMING contract ref)
        return f"{self.id}@{self.version}"


class TemplateRef(BaseModel):
    """The matched template. ``id`` is null when nothing matched (tier 3/4)."""

    model_config = ConfigDict(extra="forbid")

    sig: str
    id: str | None = None


class ClassHint(BaseModel):
    """Tier-3 only: a guess that never becomes ``class_uid``."""

    model_config = ConfigDict(extra="forbid")

    class_uid: int
    confidence: Confidence


class TimeInfo(BaseModel):
    """How the event time was established."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["event", "received"]
    tz_assumed: str | None = None
    year_inferred: bool = False
    clock_skew_ms: int | None = None


class EncodingInfo(BaseModel):
    """What the decoder found."""

    model_config = ConfigDict(extra="forbid")

    detected: str
    confidence: float = Field(ge=0.0, le=1.0)
    invalid_bytes: int = 0


class Ulpf(BaseModel):
    """IF-ULPF — the lineage extension on every normalized event."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    event_uid: str
    tenant_id: str
    source_id: str
    vendor: str
    zone: Zone
    raw_ref: RawRef
    raw_sha256: Sha256Hex
    received_time: str
    custody: Custody
    contract: ContractRef | None = None
    template: TemplateRef | None = None
    tier: int = Field(ge=1, le=4)
    conformance: Conformance
    parse_path: list[str] = Field(default_factory=list)
    # ocsf_path -> [start, end) byte offsets into the decoded raw bytes
    field_offsets: dict[str, tuple[int, int]] = Field(default_factory=dict)
    # ocsf_path -> "const" | "vocab:<name>" | "ts:<detail>" | "enrich" | "base64"
    derived_fields: dict[str, str] = Field(default_factory=dict)
    class_hint: ClassHint | None = None
    time: TimeInfo
    encoding: EncodingInfo
    pii_fields: list[str] = Field(default_factory=list)
    revision: int = Field(default=1, ge=1)
    supersedes: str | None = None
    replay: bool = False
    shadow: bool = False
    engine_version: str


class NormEvent(BaseModel):
    """IF-NORM-EVENT — an OCSF event with ``ulpf``.

    Extra keys are allowed and expected: the OCSF body is open, so every class field
    lands here alongside ``raw_data``, ``unmapped`` and ``observables``.
    """

    model_config = ConfigDict(extra="allow")

    class_uid: int
    category_uid: int
    type_uid: int
    activity_id: int
    severity_id: int
    time: int  # epoch milliseconds, never a float (deterministic serialization)
    message: str | None = None
    raw_data: str
    observables: list[dict[str, Any]] = Field(default_factory=list)
    unmapped: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    ulpf: Ulpf
