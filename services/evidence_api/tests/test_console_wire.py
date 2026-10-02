"""The lineage wire shape must match what the console was written against.

`GET /lineage/overview` and `/lineage/sources` had two code paths with two different shapes:
the ClickHouse path dumped `veyra_lineage.models` verbatim, the vault-only fallback returned a
hand-written dict in the console's shape. The console understood only the fallback, so the
pages worked on made-up numbers and broke as soon as ClickHouse had rows — `as_of` undefined
blanked the Overview and `vault.chain_ok` undefined printed "chain broken".

These tests read the console's own TypeScript interfaces and compare them to the adapter's
output, so neither side can move without the other noticing. The alternative — a JSON fixture
copied into both suites — is what let the drift happen in the first place.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from evidence_api import console

from veyra_lineage.models import (
    Overview,
    RouteStatus,
    SourceHealth,
    SourceStrip,
    TierTotals,
    VaultStatus,
    WindowRootInfo,
)

TYPES_TS = Path(__file__).resolve().parents[3] / "console" / "src" / "api" / "types.ts"


def ts_interface(name: str) -> dict[str, bool]:
    """``{field: required}`` for one `export interface` in the console's types.

    Deliberately simple: the interfaces this reads are flat records of scalar or inline
    object fields, and anything more would be a reason to share a schema instead.
    """
    text = TYPES_TS.read_text(encoding="utf-8")
    match = re.search(rf"export interface {name} \{{(.*?)\n\}}", text, re.S)
    assert match, f"no `export interface {name}` in {TYPES_TS}"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    fields: dict[str, bool] = {}
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 0:
            found = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(\??):", stripped)
            if found:
                fields[found.group(1)] = found.group(2) != "?"
        depth += stripped.count("{") - stripped.count("}")
    return fields


def sample_overview() -> Overview:
    now = datetime(2026, 10, 2, 9, 30, tzinfo=UTC)
    return Overview(
        tenant="t_ntro_core",
        generated_at=now,
        eps_1m=12.5,
        totals_by_tier=TierTotals(tier1=100, tier2=10, tier3=4, tier4=1),
        sources=[
            SourceStrip(
                tenant_id="t_ntro_core",
                source_id="src_lnx_core_07",
                eps_1m=3.5,
                raw_15m=3150,
                bytes_15m=512000,
                tiers=TierTotals(tier1=90, tier3=4),
                last_seen=now,
            )
        ],
        routes=[
            RouteStatus(
                route_id="wazuh_main",
                delivered=900,
                failed=1,
                filtered=0,
                last_at=now,
                status="ok",
            )
        ],
        vault=VaultStatus(
            segments=7,
            last_sealed_at=now,
            last_root=WindowRootInfo(
                window_id="w-1",
                window_end=now,
                leaf_count=42,
                root="ab" * 32,
                immudb_verified=False,
            ),
        ),
    )


def sample_source_health() -> SourceHealth:
    return SourceHealth(
        tenant_id="t_ntro_core",
        source_id="src_lnx_core_07",
        expected_eps=4.0,
        actual_eps=3.5,
        last_seen=datetime(2026, 10, 2, 9, 30, tzinfo=UTC),
        tier_mix=TierTotals(tier1=90, tier3=4),
        contract_ref="linux_sshd@1",
        clock_skew_p50_ms=12.0,
        status="ok",
    )


# ---------------------------------------------------------------- shape, both paths
def test_the_overview_payload_has_every_field_the_console_requires() -> None:
    payload = console.overview_payload(sample_overview())
    for field, required in ts_interface("Overview").items():
        if required:
            assert field in payload, f"console Overview.{field} is missing from the wire"


def test_the_overview_payload_adds_nothing_the_console_does_not_declare() -> None:
    payload = console.overview_payload(sample_overview())
    declared = set(ts_interface("Overview"))
    assert set(payload) <= declared, f"undeclared field(s): {sorted(set(payload) - declared)}"


@pytest.mark.parametrize(
    "interface,key", [("OverviewSource", "sources"), ("OverviewRoute", "routes")]
)
def test_the_nested_overview_rows_match_their_interfaces(interface: str, key: str) -> None:
    row = console.overview_payload(sample_overview())[key][0]
    declared = ts_interface(interface)
    for field, required in declared.items():
        if required:
            assert field in row, f"console {interface}.{field} is missing from the wire"
    assert set(row) <= set(declared)


def test_the_vault_block_carries_the_chain_verdict() -> None:
    """`chain_ok` missing is what printed "chain broken" in red during Beat 1."""
    payload = console.overview_payload(sample_overview(), chain_ok=True)
    assert payload["vault"]["chain_ok"] is True
    assert "chain_ok" in ts_interface("VaultStatus")


def test_an_unknown_chain_verdict_is_null_not_a_guess() -> None:
    payload = console.overview_payload(sample_overview(), chain_ok=None)
    assert payload["vault"]["chain_ok"] is None


def test_the_tiers_are_keyed_the_way_the_console_indexes_them() -> None:
    payload = console.overview_payload(sample_overview())
    assert payload["totals_by_tier"] == {"1": 100, "2": 10, "3": 4, "4": 1}
    assert payload["sources"][0]["tiers"] == {"1": 90, "2": 0, "3": 4, "4": 0}


def test_as_of_is_an_iso_timestamp_the_console_can_parse() -> None:
    payload = console.overview_payload(sample_overview())
    assert payload["as_of"] == "2026-10-02T09:30:00Z"
    assert datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00"))


def test_route_rates_come_from_the_index_and_lag_is_null_rather_than_invented() -> None:
    payload = console.overview_payload(sample_overview(), route_rates={"wazuh_main": (180.0, 0.2)})
    route = payload["routes"][0]
    assert route["delivered_per_min"] == 180.0
    assert route["failed_per_min"] == 0.2
    assert route["lag_s"] is None and route["breaker"] is None


def test_zone_comes_from_the_envelope_the_collector_stamped() -> None:
    payload = console.overview_payload(
        sample_overview(), dimensions={"src_lnx_core_07": ("core", "syslog_udp")}
    )
    assert payload["sources"][0]["zone"] == "core"


def test_the_source_health_payload_matches_the_console_interface() -> None:
    rows = console.source_health_payload(
        [sample_source_health()], dimensions={"src_lnx_core_07": ("core", "syslog_udp")}
    )
    declared = ts_interface("SourceHealth")
    row = rows[0]
    for field, required in declared.items():
        if required:
            assert field in row, f"console SourceHealth.{field} is missing from the wire"
    assert row["zone"] == "core"
    assert row["transport"] == "syslog_udp"
    assert row["tiers"] == {"1": 90, "2": 0, "3": 4, "4": 0}


# ---------------------------------------------------------------- the degraded path
def test_the_fallback_has_the_same_shape_as_the_indexed_path() -> None:
    indexed = console.overview_payload(sample_overview())
    fallback = console.empty_overview_payload()
    assert set(indexed) == set(fallback)
    assert set(indexed["vault"]) == set(fallback["vault"])


def test_the_fallback_reports_zeros_and_never_invents_traffic() -> None:
    """It used to answer 15 EPS and 1500/200/80/20, which is why nobody noticed the drift."""
    fallback = console.empty_overview_payload(segments=3)
    assert fallback["eps_1m"] == 0.0
    assert fallback["totals_by_tier"] == {"1": 0, "2": 0, "3": 0, "4": 0}
    assert fallback["sources"] == [] and fallback["routes"] == []
    assert fallback["vault"]["segments"] == 3
    assert fallback["vault"]["chain_ok"] is None
