"""IF-CONTROL — the compacted ``control`` topic: keys and their values.

Every consumer (normalizer, gateway, router) rebuilds its state by reading this topic
from the beginning at startup, then keeps following it. A ``None`` value is a tombstone.

The compiled-contract body (IF-CONTRACT-COMPILED) stays a dict here: ``veyra_contracts``
(owner C) owns its schema, and pinning it twice would make every compiler change a
breaking change for this package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from veyra_common.models.envelope import Transport, Zone

ContractState = Literal["draft", "testing", "canary", "active", "retired"]
KeyStatus = Literal["active", "revoked"]
SourceStatus = Literal["active", "paused"]

KEY_PREFIX_CONTRACT = "contract:"
KEY_PREFIX_APIKEY = "apikey:"
KEY_PREFIX_SOURCE = "source:"
KEY_PREFIX_VOCAB = "vocab:"
KEY_PREFIX_ENRICH = "enrich:"
KEY_ROUTES = "routes"


class CandidateContract(BaseModel):
    """The canary version running in shadow next to the active one (A5)."""

    model_config = ConfigDict(extra="forbid")

    version: int
    compiled: dict[str, Any]


class ContractMessage(BaseModel):
    """``contract:<id>`` — the active compiled contract, plus an optional candidate."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: int
    state: ContractState
    tenant_id: str
    sources: list[str] = Field(default_factory=list)
    compiled: dict[str, Any]
    candidate: CandidateContract | None = None
    published_at: str


class ApiKeyMessage(BaseModel):
    """``apikey:<key_id>`` — the gateway's auth registry (A2)."""

    model_config = ConfigDict(extra="forbid")

    key_id: str
    secret_sha256: str
    pepper_id: str
    source_id: str
    tenant_id: str
    status: KeyStatus = "active"
    quota_eps: int
    created_at: str


class SourceMessage(BaseModel):
    """``source:<source_id>`` — the source inventory as the data plane sees it."""

    model_config = ConfigDict(extra="forbid")

    source_id: str
    tenant_id: str
    vendor: str
    zone: Zone
    transport: Transport
    contract_id: str | None = None
    expected_eps: float = 0.0
    salt_buckets: int = Field(default=1, ge=1)
    status: SourceStatus = "active"


class VocabMessage(BaseModel):
    """``vocab:<name>`` — e.g. status words to OCSF enum values."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    entries: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EnrichMessage(BaseModel):
    """``enrich:<table>`` — small offline tables (asset inventory, zone map)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: int
    rows: list[dict[str, Any]] = Field(default_factory=list)


class RouteSpec(BaseModel):
    """One route in IF-ROUTES (owner A; the sink/filter bodies stay open)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    filter: dict[str, Any] = Field(default_factory=dict)
    format: str = "ocsf_json"
    masking: dict[str, Any] | Literal["none"] = "none"
    sink: dict[str, Any]


class RoutesMessage(BaseModel):
    """``routes`` — the full IF-ROUTES document."""

    model_config = ConfigDict(extra="forbid")

    routes: list[RouteSpec] = Field(default_factory=list)


def control_key(kind: str, name: str | None = None) -> str:
    """Build a control key: ``control_key("contract", "authsrv") == "contract:authsrv"``."""
    if kind == "routes":
        return KEY_ROUTES
    if name is None:
        raise ValueError(f"control key kind {kind!r} needs a name")
    return f"{kind}:{name}"
