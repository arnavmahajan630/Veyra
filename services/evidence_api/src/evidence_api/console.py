"""One wire shape for the console's Overview, Sources and Delivery panels.

``GET /lineage/overview`` and ``GET /lineage/sources`` used to answer with two different
shapes: the ClickHouse path returned :mod:`veyra_lineage.models` verbatim
(``generated_at``, ``tier1..tier4``, ``eps_1m``, ``tier_mix``), while the vault-only fallback
returned a hand-written dict in the console's shape (``as_of``, ``"1".."4"``, ``eps``,
``tiers``, ``chain_ok``, ``tier_history``). The console was written against the fallback, so
the pages worked on made-up numbers and broke the moment ClickHouse had data: ``as_of``
undefined blanked the Overview, and ``vault.chain_ok`` undefined rendered "chain broken".

Everything now goes through the adapters here, so there is one shape and it cannot drift
again. Fields the index genuinely does not hold (a route's lag and breaker state) are ``None``
rather than invented: the console's types allow null, and a made-up number on a judged screen
is worse than a blank.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from veyra_lineage.models import Overview, SourceHealth, TierTotals


def tier_counts(tiers: TierTotals) -> dict[str, int]:
    """``TierTotals`` as the console's ``Record<"1".."4", number>``."""
    return {"1": tiers.tier1, "2": tiers.tier2, "3": tiers.tier3, "4": tiers.tier4}


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat().replace("+00:00", "Z")


def empty_tier_counts() -> dict[str, int]:
    return {"1": 0, "2": 0, "3": 0, "4": 0}


def overview_payload(
    overview: Overview,
    *,
    dimensions: dict[str, tuple[str, str]] | None = None,
    route_rates: dict[str, tuple[float, float]] | None = None,
    history: list[TierTotals] | None = None,
    chain_ok: bool | None = None,
) -> dict[str, Any]:
    """The Overview page's payload (IF-API-EVIDENCE, console `Overview`)."""
    dims = dimensions or {}
    rates = route_rates or {}
    return {
        "as_of": _iso(overview.generated_at) or _iso(datetime.now(UTC)),
        "eps_1m": overview.eps_1m,
        "totals_by_tier": tier_counts(overview.totals_by_tier),
        "sources": [
            {
                "source_id": source.source_id,
                "zone": dims.get(source.source_id, ("", ""))[0],
                "eps": source.eps_1m,
                "tiers": tier_counts(source.tiers),
                "last_seen": _iso(source.last_seen),
            }
            for source in overview.sources
        ],
        "routes": [
            {
                "route_id": route.route_id,
                "delivered_per_min": rates.get(route.route_id, (0.0, 0.0))[0],
                "failed_per_min": rates.get(route.route_id, (0.0, 0.0))[1],
                # Neither is in the lineage index: the router owns them. Null, not a guess.
                "lag_s": None,
                "breaker": None,
            }
            for route in overview.routes
        ],
        "vault": vault_payload(overview, chain_ok=chain_ok),
        "tier_history": [tier_counts(sample) for sample in (history or [])],
    }


def vault_payload(overview: Overview, *, chain_ok: bool | None = None) -> dict[str, Any]:
    last_root = overview.vault.last_root
    return {
        "segments": overview.vault.segments,
        "last_sealed_at": _iso(overview.vault.last_sealed_at),
        "last_root": None
        if last_root is None
        else {
            "window_id": last_root.window_id,
            "window_end": _iso(last_root.window_end),
            "immudb_verified": last_root.immudb_verified,
        },
        # Unknown stays unknown: the console shows "chain unknown", never a green it did not
        # earn, and never a red just because the field was missing.
        "chain_ok": chain_ok,
    }


def source_health_payload(
    rows: list[SourceHealth], *, dimensions: dict[str, tuple[str, str]] | None = None
) -> list[dict[str, Any]]:
    """The Sources page's payload (console `SourceHealth`)."""
    dims = dimensions or {}
    out: list[dict[str, Any]] = []
    for row in rows:
        zone, transport = dims.get(row.source_id, ("", ""))
        out.append(
            {
                "source_id": row.source_id,
                "tenant_id": row.tenant_id,
                "zone": zone,
                "transport": transport,
                "contract_ref": row.contract_ref,
                "expected_eps": row.expected_eps if row.expected_eps is not None else 0.0,
                "actual_eps": row.actual_eps,
                "last_seen": _iso(row.last_seen),
                "tiers": tier_counts(row.tier_mix),
                "clock_skew_p50_ms": row.clock_skew_p50_ms,
                # Kept so the drawer can still show the index's own verdict.
                "status": row.status,
            }
        )
    return out


def empty_overview_payload(
    *,
    segments: int = 0,
    last_sealed_at: str | None = None,
    last_root: dict[str, Any] | None = None,
    chain_ok: bool | None = None,
) -> dict[str, Any]:
    """The payload when there is no lineage index to ask.

    Real zeros and a null chain verdict. The previous version of this returned 15 EPS and
    1500/200/80/20, which is what let a broken wire shape go unnoticed for a whole phase.
    """
    return {
        "as_of": _iso(datetime.now(UTC)),
        "eps_1m": 0.0,
        "totals_by_tier": empty_tier_counts(),
        "sources": [],
        "routes": [],
        "vault": {
            "segments": segments,
            "last_sealed_at": last_sealed_at,
            "last_root": last_root,
            "chain_ok": chain_ok,
        },
        "tier_history": [],
    }
