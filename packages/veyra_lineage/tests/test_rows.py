"""Record -> row mapping (B1). No ClickHouse needed.

Every builder is tested against ``veyra_common/fixtures/*.json``, the cross-track contract
surface: if a producer changes a record shape, these fail on purpose (01_TEAM_GUIDE §7).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest

from veyra_common.envelope import stamp
from veyra_lineage import rows as R

FIXTURES = Path(__file__).resolve().parents[2] / "veyra_common" / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


POS = R.KafkaPos("raw.custom", 1, 4412, 1790000000123)


def row_of(spec: R.TableSpec, row: R.Row) -> dict[str, Any]:
    assert len(row) == len(spec.columns), (
        f"{spec.name}: {len(row)} values, {len(spec.columns)} columns"
    )
    return dict(zip(spec.columns, row, strict=True))


# ---------------------------------------------------------------- timestamps
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-26T08:35:11.123456789Z", 1790411711_123456789),
        ("2026-09-26T08:35:11Z", 1790411711_000000000),
        ("2026-09-26T08:35:11.5Z", 1790411711_500000000),
        ("2026-09-26T08:35:11.123456789+00:00", 1790411711_123456789),
        # A non-UTC offset must be converted, not ignored.
        ("2026-09-26T14:05:11.123456789+05:30", 1790411711_123456789),
    ],
)
def test_rfc3339_to_ns_keeps_every_digit(text: str, expected: int) -> None:
    assert R.rfc3339_to_ns(text) == expected


def test_rfc3339_to_ns_rejects_a_naive_timestamp() -> None:
    """The edge always stamps UTC; a missing zone means a bug upstream, not local time."""
    with pytest.raises(ValueError, match="timezone"):
        R.rfc3339_to_ns("2026-09-26T08:35:11.123456789")


def test_nanosecond_precision_survives_the_envelope_fixture() -> None:
    env = json.loads(fixture("envelope.json"))
    ns = R.rfc3339_to_ns(env["received_time"])
    assert str(ns).endswith(env["received_time"].split(".")[1].rstrip("Z").ljust(9, "0")[-3:])


# ---------------------------------------------------------------- raw preview
def test_raw_preview_is_capped_at_256_characters() -> None:
    raw = ("x" * 5000).encode()
    preview = R.raw_preview(base64.b64encode(raw).decode())
    assert len(preview) == R.RAW_PREVIEW_CHARS == 256


def test_raw_preview_keeps_short_payloads_whole() -> None:
    raw = b"<134>Sep 26 14:05:11 fw01 app[233]: user=a.sharma FAILED"
    assert R.raw_preview(base64.b64encode(raw).decode()) == raw.decode()


def test_raw_preview_handles_multibyte_and_invalid_bytes() -> None:
    """Enough base64 must be decoded for 256 *characters*, and garbage must not raise."""
    raw = ("€" * 400).encode()  # 3 bytes per character
    assert R.raw_preview(base64.b64encode(raw).decode()) == "€" * 256
    assert R.raw_preview(base64.b64encode(b"\xff\xfe\x00 abc").decode()).endswith(" abc")


def test_raw_row_never_stores_raw_bytes() -> None:
    """P1/IF-CH-SCHEMA: the index holds metadata and a preview, never the evidence."""
    raw = b"secret-payload-" + b"z" * 4000
    env = stamp(
        raw,
        collector_id="t",
        transport="syslog_udp",
        framing_method="datagram",
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
    )
    spec, row = R.raw_row(env.model_dump_json().encode(), POS)
    values = row_of(spec, row)
    assert "raw_b64" not in spec.columns
    assert env.raw_b64 not in str(values)
    assert len(values["raw_preview"]) == 256
    assert values["raw_len"] == len(raw)
    assert values["raw_sha256"] == env.raw_sha256


# ---------------------------------------------------------------- builders
def test_raw_row_from_fixture() -> None:
    spec, row = R.raw_row(fixture("envelope.json"), POS)
    values = row_of(spec, row)
    env = json.loads(fixture("envelope.json"))
    assert spec is R.RAW_EVENTS
    assert values["event_uid"] == env["event_uid"]
    assert values["received_time"] == R.rfc3339_to_ns(env["received_time"])
    assert values["framing_method"] == env["framing"]["method"]
    assert values["auth_method"] == env["auth"]["method"]
    # The Kafka position, not the envelope's own raw_ref: this is where the record sat.
    assert (values["raw_topic"], values["raw_partition"], values["raw_offset"]) == (
        POS.topic,
        POS.partition,
        POS.offset,
    )


def test_lineage_row_from_fixture_keeps_search_terms() -> None:
    spec, row = R.lineage_row(fixture("lineage.json"), POS)
    values = row_of(spec, row)
    rec = json.loads(fixture("lineage.json"))
    assert spec is R.NORM_LINEAGE
    assert values["search_terms"] == rec["search_terms"]
    assert "103.21.4.77" in values["search_terms"]
    assert values["contract_ref"] == rec["contract_ref"]
    assert values["template_id"] == ""  # null -> '' (the column is LowCardinality)
    assert values["replay_job_id"] == ""


def test_norm_row_keeps_the_event_verbatim() -> None:
    payload = fixture("norm_event.json")
    spec, row = R.norm_row(payload, POS)
    values = row_of(spec, row)
    assert spec is R.NORM_EVENTS
    # The stored JSON must be byte-identical: B4 serves it and verify re-reads it.
    assert values["ocsf_json"] == payload.decode()
    event = json.loads(payload)
    assert values["event_uid"] == event["ulpf"]["event_uid"]
    assert values["revision"] == event["ulpf"]["revision"]
    assert values["clock_skew_ms"] == event["ulpf"]["time"]["clock_skew_ms"]


def test_vault_index_dispatches_on_kind() -> None:
    spec, row = R.vault_index_row(fixture("vault_index_event.json"), POS)
    assert spec is R.VAULT_LOCATIONS
    assert row_of(spec, row)["sealed"] is True
    spec, row = R.vault_index_row(fixture("vault_index_segment.json"), POS)
    assert spec is R.SEGMENTS
    assert row_of(spec, row)["record_count"] == 32


@pytest.mark.parametrize(
    ("builder", "name", "spec"),
    [
        (R.receipt_row, "receipt.json", R.RECEIPTS),
        (R.dlq_row, "dlq.json", R.DLQ_EVENTS),
        (R.shadow_row, "shadow.json", R.SHADOW_DIFFS),
        (R.audit_row, "audit.json", R.AUDIT_LOG),
    ],
)
def test_remaining_builders_produce_their_table(
    builder: R.Builder, name: str, spec: R.TableSpec
) -> None:
    got_spec, row = builder(fixture(name), POS)
    assert got_spec is spec
    values = row_of(spec, row)
    assert values["kafka_offset"] == POS.offset
    assert values["kafka_topic"] == POS.topic


def test_a_record_that_breaks_its_contract_is_rejected() -> None:
    """The indexer relies on this to skip a bad record instead of inserting junk."""
    with pytest.raises(Exception):  # noqa: B017 - pydantic/json, both are failures
        R.lineage_row(b'{"event_uid": "x"}', POS)


# ---------------------------------------------------------------- dispatch
@pytest.mark.parametrize(
    ("topic", "builder"),
    [
        ("raw.custom", R.raw_row),
        ("raw.unregistered", R.raw_row),
        ("norm.iam", R.norm_row),
        ("norm.uncategorized", R.norm_row),
        ("lineage", R.lineage_row),
        ("vault_index", R.vault_index_row),
        ("receipts", R.receipt_row),
        ("dlq", R.dlq_row),
        ("shadow", R.shadow_row),
        ("audit", R.audit_row),
    ],
)
def test_every_subscribed_topic_has_a_builder(topic: str, builder: R.Builder) -> None:
    assert R.builder_for(topic) is builder
    assert R.tables_for(topic)


def test_unknown_topics_are_not_indexed() -> None:
    for topic in ("control", "replay.raw", "rawish", "nope"):
        assert R.builder_for(topic) is None
        assert R.tables_for(topic) == ()
    with pytest.raises(KeyError):
        R.build_row("control", b"{}", POS)


def test_vault_index_maps_to_both_of_its_tables() -> None:
    assert set(R.tables_for("vault_index")) == {R.VAULT_LOCATIONS, R.SEGMENTS}


def test_every_spec_declares_its_kafka_position_columns() -> None:
    for spec in R.TABLES.values():
        assert set(spec.pos_columns) <= set(spec.columns), spec.name


# ---------------------------------------------------------------- upstream junk
def test_out_of_range_integers_are_clamped_not_fatal() -> None:
    """Real collectors report ports above 65535. IF-ENVELOPE allows any int, so the row
    must still build — an event is never lost over a silly field (P2)."""
    env = stamp(
        b"<134>Sep 26 14:05:11 fw01 app[233]: junk port",
        collector_id="t",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_fw_dmz_01",
        tenant_id="t_ntro_core",
        vendor="acme_ngfw",
        zone="dmz",
        peer_ip="172.20.0.21",
    )
    payload = env.model_dump(mode="json") | {"peer_port": 98764, "salt": 2**40}
    spec, row = R.raw_row(json.dumps(payload).encode(), POS)
    values = row_of(spec, row)
    # An impossible-but-small port is stored faithfully rather than mangled...
    assert values["peer_port"] == 98764
    # ...and only a value too wide for the column is clamped.
    assert values["salt"] == 2**32 - 1


def test_a_missing_peer_port_stays_null() -> None:
    """None must not become 0: "no port" and "port 0" are different facts."""
    env = stamp(
        b"<134>Sep 26 14:05:11 fw01 app[233]: no port",
        collector_id="t",
        transport="syslog_udp",
        framing_method="datagram",
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
    )
    spec, row = R.raw_row(env.model_dump_json().encode(), POS)
    assert row_of(spec, row)["peer_port"] is None
