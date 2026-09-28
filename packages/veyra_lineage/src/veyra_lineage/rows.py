"""Kafka record -> ClickHouse row mapping for every indexed table (IF-CH-SCHEMA).

The lineage indexer is the only writer, so the mapping lives next to the schema: a
column added in a migration and a builder here change in the same PR.

Every builder validates the record against its ``veyra_common.models`` contract first,
then returns one tuple in :attr:`TableSpec.columns` order. ``None`` optional strings
become ``''`` (LowCardinality columns are not Nullable); the query library maps them
back. Timestamps are integer ticks: nanoseconds for ``DateTime64(9)`` (so the edge's
nanosecond ``received_time`` survives) and milliseconds for ``kafka_ts``.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import TypeAdapter

from veyra_common.models import (
    AuditRecord,
    DlqRecord,
    Envelope,
    LineageRecord,
    NormEvent,
    Receipt,
    ShadowRecord,
    VaultIndexEvent,
    VaultIndexRecord,
    VaultIndexSegment,
)
from veyra_common.topics import (
    NORM_PREFIX,
    RAW_PREFIX,
    TOPIC_AUDIT,
    TOPIC_DLQ,
    TOPIC_LINEAGE,
    TOPIC_RECEIPTS,
    TOPIC_SHADOW,
    TOPIC_VAULT_INDEX,
)

RAW_PREVIEW_CHARS = 256
# UTF-8 needs at most 4 bytes per character, and 4 base64 chars carry 3 bytes.
_PREVIEW_B64_CHARS = -(-RAW_PREVIEW_CHARS * 4 // 3) * 4

Row = tuple[Any, ...]
Builder = Callable[[bytes, "KafkaPos"], tuple["TableSpec", Row]]


@dataclass(frozen=True, slots=True)
class KafkaPos:
    """Where a record came from. Stored on every row, so a restart can skip records
    that were inserted but whose offsets were never committed."""

    topic: str
    partition: int
    offset: int
    ts_ms: int


@dataclass(frozen=True, slots=True)
class TableSpec:
    name: str
    columns: tuple[str, ...]
    # The columns holding KafkaPos (topic, partition, offset, ts) for the replay guard.
    pos_columns: tuple[str, str, str, str] = (
        "kafka_topic",
        "kafka_partition",
        "kafka_offset",
        "kafka_ts",
    )


_POS = ("kafka_topic", "kafka_partition", "kafka_offset", "kafka_ts")

RAW_EVENTS = TableSpec(
    "raw_events",
    (
        "event_uid",
        "tenant_id",
        "source_id",
        "vendor",
        "zone",
        "collector_id",
        "transport",
        "listener",
        "peer_ip",
        "peer_port",
        "custody",
        "auth_method",
        "auth_key_id",
        "received_time",
        "seq_no",
        "raw_topic",
        "raw_partition",
        "raw_offset",
        "kafka_ts",
        "raw_sha256",
        "raw_len",
        "framing_method",
        "framing_truncated",
        "framing_parts",
        "salt",
        "raw_preview",
    ),
    pos_columns=("raw_topic", "raw_partition", "raw_offset", "kafka_ts"),
)
NORM_EVENTS = TableSpec(
    "norm_events",
    (
        "event_uid",
        "revision",
        "tenant_id",
        "source_id",
        "class_uid",
        "tier",
        "received_time",
        "clock_skew_ms",
        "ocsf_json",
        *_POS,
    ),
)
NORM_LINEAGE = TableSpec(
    "norm_lineage",
    (
        "event_uid",
        "revision",
        "tenant_id",
        "source_id",
        "raw_topic",
        "raw_partition",
        "raw_offset",
        "raw_sha256",
        "contract_ref",
        "template_sig",
        "template_id",
        "tier",
        "conformance",
        "class_uid",
        "category",
        "norm_topic",
        "produced_at",
        "replay",
        "replay_job_id",
        "search_terms",
        *_POS,
    ),
)
VAULT_LOCATIONS = TableSpec(
    "vault_locations",
    (
        "event_uid",
        "raw_topic",
        "raw_partition",
        "raw_offset",
        "segment_id",
        "record_idx",
        "chain_hash",
        "sealed",
        "sealed_at",
        *_POS,
    ),
)
SEGMENTS = TableSpec(
    "segments",
    (
        "segment_id",
        "topic",
        "partition",
        "first_offset",
        "last_offset",
        "record_count",
        "prev_chain_hash",
        "last_chain_hash",
        "digest",
        "sealed_at",
        *_POS,
    ),
)
RECEIPTS = TableSpec(
    "receipts",
    ("event_uid", "revision", "route_id", "status", "detail", "at", *_POS),
)
DLQ_EVENTS = TableSpec(
    "dlq_events",
    (
        "event_uid",
        "tenant_id",
        "source_id",
        "tier",
        "reason_code",
        "reason_detail",
        "contract_ref",
        "template_sig",
        "text_masked",
        "parse_path",
        "produced_at",
        *_POS,
    ),
)
SHADOW_DIFFS = TableSpec(
    "shadow_diffs",
    (
        "event_uid",
        "contract_id",
        "active_ref",
        "candidate_ref",
        "active_tier",
        "candidate_tier",
        "changed_fields",
        "regressions",
        "produced_at",
        *_POS,
    ),
)
AUDIT_LOG = TableSpec(
    "audit_log",
    ("actor", "role", "action", "target", "detail", "at", *_POS),
)

TABLES: dict[str, TableSpec] = {
    t.name: t
    for t in (
        RAW_EVENTS,
        NORM_EVENTS,
        NORM_LINEAGE,
        VAULT_LOCATIONS,
        SEGMENTS,
        RECEIPTS,
        DLQ_EVENTS,
        SHADOW_DIFFS,
        AUDIT_LOG,
    )
}


# ---------------------------------------------------------------- time
def rfc3339_to_ns(value: str) -> int:
    """``2026-09-26T08:35:11.123456789Z`` -> epoch nanoseconds, without losing digits.

    ``datetime`` stops at microseconds, so the fraction is handled separately.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    frac_ns = 0
    dot = text.find(".")
    if dot != -1:
        end = dot + 1
        while end < len(text) and text[end].isdigit():
            end += 1
        digits = text[dot + 1 : end]
        frac_ns = int((digits + "000000000")[:9]) if digits else 0
        text = text[:dot] + text[end:]
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without a timezone: {value!r}")
    return int(parsed.timestamp()) * 1_000_000_000 + frac_ns


_UINT32_MAX = 2**32 - 1


def _uint32(value: int | None) -> int | None:
    """Clamp an upstream integer into ``UInt32``, keeping ``None`` as ``None``.

    IF-ENVELOPE puts no range on ``peer_port``/``parts``/``salt``, and real collectors do
    report nonsense (ports above 65535 have been seen). A silly value must land in the
    index as a silly value, never as a failed insert that stalls the whole batch.
    """
    if value is None:
        return None
    return min(max(int(value), 0), _UINT32_MAX)


def raw_preview(raw_b64: str) -> str:
    """The first 256 decoded characters. Decodes only the prefix it needs."""
    head = base64.b64decode(raw_b64[:_PREVIEW_B64_CHARS])
    return head.decode("utf-8", errors="replace")[:RAW_PREVIEW_CHARS]


# ---------------------------------------------------------------- builders
_VAULT_INDEX = TypeAdapter(VaultIndexRecord)


def _pos(p: KafkaPos) -> tuple[str, int, int, int]:
    return (p.topic, p.partition, p.offset, p.ts_ms)


def raw_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    env = Envelope.model_validate_json(value)
    return RAW_EVENTS, (
        env.event_uid,
        env.tenant_id,
        env.source_id,
        env.vendor,
        env.zone,
        env.collector_id,
        env.transport,
        env.listener or "",
        env.peer_ip or "",
        _uint32(env.peer_port),
        env.custody,
        env.auth.method,
        env.auth.key_id or "",
        rfc3339_to_ns(env.received_time),
        env.seq_no,
        pos.topic,
        pos.partition,
        pos.offset,
        pos.ts_ms,
        env.raw_sha256,
        env.raw_len,
        env.framing.method,
        env.framing.truncated,
        _uint32(env.framing.parts),
        _uint32(env.salt),
        raw_preview(env.raw_b64),
    )


def norm_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    event = NormEvent.model_validate_json(value)
    u = event.ulpf
    return NORM_EVENTS, (
        u.event_uid,
        u.revision,
        u.tenant_id,
        u.source_id,
        event.class_uid,
        u.tier,
        rfc3339_to_ns(u.received_time),
        u.time.clock_skew_ms,
        value.decode("utf-8"),
        *_pos(pos),
    )


def lineage_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = LineageRecord.model_validate_json(value)
    return NORM_LINEAGE, (
        rec.event_uid,
        rec.revision,
        rec.tenant_id,
        rec.source_id,
        rec.raw_ref.topic,
        rec.raw_ref.partition,
        rec.raw_ref.offset,
        rec.raw_sha256,
        rec.contract_ref or "",
        rec.template_sig,
        rec.template_id or "",
        rec.tier,
        rec.conformance,
        rec.class_uid,
        rec.category,
        rec.norm_topic,
        rfc3339_to_ns(rec.produced_at),
        rec.replay,
        rec.replay_job_id or "",
        list(rec.search_terms),
        *_pos(pos),
    )


def vault_index_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = _VAULT_INDEX.validate_json(value)
    if isinstance(rec, VaultIndexEvent):
        return VAULT_LOCATIONS, (
            rec.event_uid,
            rec.raw_topic,
            rec.partition,
            rec.offset,
            rec.segment_id,
            rec.record_idx,
            rec.chain_hash_hex,
            True,
            rfc3339_to_ns(rec.sealed_at),
            *_pos(pos),
        )
    assert isinstance(rec, VaultIndexSegment)
    return SEGMENTS, (
        rec.segment_id,
        rec.raw_topic,
        rec.partition,
        rec.first_offset,
        rec.last_offset,
        rec.record_count,
        rec.prev_chain_hash_hex,
        rec.last_chain_hash_hex,
        rec.segment_digest_hex,
        rfc3339_to_ns(rec.sealed_at),
        *_pos(pos),
    )


def receipt_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = Receipt.model_validate_json(value)
    return RECEIPTS, (
        rec.event_uid,
        rec.revision,
        rec.route_id,
        rec.status,
        rec.detail,
        rfc3339_to_ns(rec.at),
        *_pos(pos),
    )


def dlq_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = DlqRecord.model_validate_json(value)
    return DLQ_EVENTS, (
        rec.event_uid,
        rec.tenant_id,
        rec.source_id,
        rec.tier,
        rec.reason_code,
        rec.reason_detail,
        rec.contract_ref or "",
        rec.template_sig,
        rec.text_masked,
        list(rec.parse_path),
        rfc3339_to_ns(rec.produced_at),
        *_pos(pos),
    )


def shadow_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = ShadowRecord.model_validate_json(value)
    return SHADOW_DIFFS, (
        rec.event_uid,
        rec.contract_id,
        rec.active_ref or "",
        rec.candidate_ref,
        rec.active_tier,
        rec.candidate_tier,
        list(rec.changed_fields),
        list(rec.regressions),
        rfc3339_to_ns(rec.produced_at),
        *_pos(pos),
    )


def audit_row(value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    rec = AuditRecord.model_validate_json(value)
    return AUDIT_LOG, (
        rec.actor,
        rec.role,
        rec.action,
        rec.target,
        rec.detail,
        rfc3339_to_ns(rec.at),
        *_pos(pos),
    )


_EXACT: dict[str, Builder] = {
    TOPIC_LINEAGE: lineage_row,
    TOPIC_VAULT_INDEX: vault_index_row,
    TOPIC_RECEIPTS: receipt_row,
    TOPIC_DLQ: dlq_row,
    TOPIC_SHADOW: shadow_row,
    TOPIC_AUDIT: audit_row,
}


def builder_for(topic: str) -> Builder | None:
    """The row builder for a topic the indexer subscribes to, else ``None``."""
    if topic.startswith(RAW_PREFIX):
        return raw_row
    if topic.startswith(NORM_PREFIX):
        return norm_row
    return _EXACT.get(topic)


def tables_for(topic: str) -> tuple[TableSpec, ...]:
    """Every table a topic's records can land in (``vault_index`` feeds two)."""
    builder = builder_for(topic)
    if builder is None:
        return ()
    if builder is vault_index_row:
        return (VAULT_LOCATIONS, SEGMENTS)
    singles: dict[Builder, tuple[TableSpec, ...]] = {
        raw_row: (RAW_EVENTS,),
        norm_row: (NORM_EVENTS,),
        lineage_row: (NORM_LINEAGE,),
        receipt_row: (RECEIPTS,),
        dlq_row: (DLQ_EVENTS,),
        shadow_row: (SHADOW_DIFFS,),
        audit_row: (AUDIT_LOG,),
    }
    return singles[builder]


def build_row(topic: str, value: bytes, pos: KafkaPos) -> tuple[TableSpec, Row]:
    builder = builder_for(topic)
    if builder is None:
        raise KeyError(f"no table for topic {topic!r}")
    return builder(value, pos)
