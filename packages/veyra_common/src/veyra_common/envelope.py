"""``stamp()`` — build an IF-ENVELOPE from raw bytes (P1: evidence before parsing).

Both ingestion paths must produce byte-identical envelopes for the same input:

* the edge (A1) stamps in VRL inside Vector;
* the gateway (A2) calls this function.

``tests/test_envelope.py`` writes ``fixtures/envelope_vectors.json`` from here, and
A1's parity test asserts Vector reproduces those ``raw_sha256``/``raw_b64`` values.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from veyra_common.framing import FramedEvent
from veyra_common.hashing import sha256_hex
from veyra_common.ids import now_ns, uuid7_str
from veyra_common.models.envelope import (
    UNREGISTERED_SOURCE,
    UNREGISTERED_TENANT,
    UNREGISTERED_VENDOR,
    Auth,
    AuthMethod,
    Custody,
    Envelope,
    Framing,
    FramingMethod,
    HecMeta,
    Transport,
    Zone,
)


def rfc3339_ns(ts_ns: int | None = None) -> str:
    """UTC RFC3339 with nanosecond precision, the ``received_time`` wire format."""
    if ts_ns is None:
        ts_ns = now_ns()  # time_ns(), not datetime: a float loses the low nanoseconds
    secs, nanos = divmod(ts_ns, 1_000_000_000)
    base = datetime.fromtimestamp(secs, UTC).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def stamp(
    raw: bytes,
    *,
    collector_id: str,
    transport: Transport,
    framing_method: FramingMethod,
    zone: Zone = "dmz",
    tenant_id: str = UNREGISTERED_TENANT,
    source_id: str = UNREGISTERED_SOURCE,
    vendor: str = UNREGISTERED_VENDOR,
    listener: str | None = None,
    peer_ip: str | None = None,
    peer_port: int | None = None,
    parts: int = 1,
    truncated: bool = False,
    custody: Custody = "realtime",
    auth_method: AuthMethod = "none",
    auth_key_id: str | None = None,
    seq_no: int | None = None,
    salt: int | None = None,
    event_uid: str | None = None,
    received_time: str | None = None,
    max_event_bytes: int | None = None,
    hec_meta: HecMeta | None = None,
) -> Envelope:
    """Stamp ``raw`` into an IF-ENVELOPE.

    ``event_uid`` and ``received_time`` may be supplied to make a test deterministic;
    in production both are generated here, once, and never recomputed (P1).

    When ``max_event_bytes`` is given and exceeded, the bytes are cut and
    ``framing.truncated`` is set — the hash covers the **stored** bytes, so a verifier
    that re-hashes what it reads still agrees.
    """
    if max_event_bytes is not None and len(raw) > max_event_bytes:
        raw = raw[:max_event_bytes]
        truncated = True

    return Envelope(
        event_uid=event_uid or uuid7_str(),
        tenant_id=tenant_id,
        source_id=source_id,
        vendor=vendor,
        zone=zone,
        collector_id=collector_id,
        transport=transport,
        peer_ip=peer_ip,
        peer_port=peer_port,
        listener=listener,
        received_time=received_time or rfc3339_ns(),
        seq_no=seq_no,
        raw_sha256=sha256_hex(raw),
        raw_len=len(raw),
        raw_b64=base64.b64encode(raw).decode("ascii"),
        framing=Framing(method=framing_method, truncated=truncated, parts=parts),
        custody=custody,
        auth=Auth(method=auth_method, key_id=auth_key_id),
        salt=salt,
        hec_meta=hec_meta,
    )


def stamp_framed(event: FramedEvent, **meta: object) -> Envelope:
    """Stamp a :class:`~veyra_common.framing.FramedEvent`, carrying its framing over."""
    meta.setdefault("framing_method", event.method)
    meta.setdefault("parts", event.parts)
    meta.setdefault("truncated", event.truncated)
    return stamp(event.raw, **meta)  # type: ignore[arg-type]
