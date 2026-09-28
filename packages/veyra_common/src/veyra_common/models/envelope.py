"""IF-ENVELOPE — the raw evidence record produced at the edge or the gateway.

Principle P1: this object is created **before any parsing**, and nothing downstream
may modify ``raw_b64``/``raw_sha256``. ``received_time`` stays a string so the
edge's nanosecond precision survives the round trip (datetime would truncate).
"""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from veyra_common.hashing import sha256_hex

Transport = Literal[
    "syslog_udp", "syslog_tcp", "http_hec_raw", "http_hec_event", "http_batch", "kafka"
]
FramingMethod = Literal[
    "datagram", "newline", "octet_counting", "multiline_join", "http_body", "batch_line"
]
Custody = Literal["realtime", "post_hoc", "post_hoc_signed"]
AuthMethod = Literal["ip_map", "api_key", "mtls", "none"]
Zone = Literal["dmz", "core", "ot", "external"]

Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
RFC3339 = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")]

UNREGISTERED_TENANT = "unassigned"
UNREGISTERED_SOURCE = "unregistered"
UNREGISTERED_VENDOR = "unregistered"


class Framing(BaseModel):
    """How the bytes were carved out of the transport."""

    model_config = ConfigDict(extra="forbid")

    method: FramingMethod
    truncated: bool = False
    parts: int = Field(default=1, ge=1)


class Auth(BaseModel):
    """How the sender was identified."""

    model_config = ConfigDict(extra="forbid")

    method: AuthMethod
    key_id: str | None = None


class HecMeta(BaseModel):
    """The Splunk-HEC metadata a shipper sends alongside the event (A2, additive in v1.4).

    These are the sender's *claims* about the event, not facts the gateway established, so they are
    kept beside the envelope rather than folded into it: ``time`` in particular must not become
    ``received_time`` (which is when VEYRA saw the bytes) nor the event time (which the engine
    derives from the bytes themselves). The normalizer surfaces this block in ``unmapped``.
    """

    model_config = ConfigDict(extra="forbid")

    time: float | None = None
    host: str | None = None
    source: str | None = None
    sourcetype: str | None = None
    index: str | None = None


class Envelope(BaseModel):
    """A stamped raw record on ``raw.<vendor>`` (IF-ENVELOPE)."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    event_uid: str
    tenant_id: str
    source_id: str
    vendor: str
    zone: Zone
    collector_id: str
    transport: Transport
    peer_ip: str | None = None
    peer_port: int | None = None
    listener: str | None = None
    received_time: RFC3339
    seq_no: int | None = None
    raw_sha256: Sha256Hex
    raw_len: int = Field(ge=0)
    raw_b64: str
    framing: Framing
    custody: Custody = "realtime"
    auth: Auth
    salt: int | None = None
    # Only set by the gateway's HEC event endpoint (IF-ENVELOPE, additive v1.4).
    hec_meta: HecMeta | None = None

    # ------------------------------------------------------------------ helpers
    @property
    def raw_bytes(self) -> bytes:
        """The exact original bytes."""
        return base64.b64decode(self.raw_b64, validate=True)

    def hash_matches(self) -> bool:
        """Recompute ``raw_sha256`` from the payload — used by every verifier."""
        return sha256_hex(self.raw_bytes) == self.raw_sha256

    def kafka_key(self) -> str:
        """``source_id``, or ``source_id#<salt>`` for a salted heavy hitter (IF-TOPICS)."""
        return f"{self.source_id}#{self.salt}" if self.salt is not None else self.source_id

    @field_validator("raw_b64")
    @classmethod
    def _valid_b64(cls, value: str) -> str:
        try:
            base64.b64decode(value, validate=True)
        except Exception as exc:
            raise ValueError(f"raw_b64 is not valid base64: {exc}") from exc
        return value


class ReplayBlock(BaseModel):
    """The extra block carried by ``replay.raw`` messages (A5)."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    revision: int = Field(ge=2)
    supersedes: str


class ReplayEnvelope(Envelope):
    """IF-ENVELOPE plus the replay block."""

    replay: ReplayBlock
