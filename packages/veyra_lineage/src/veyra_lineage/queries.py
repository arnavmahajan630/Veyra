"""The lineage query library: every SQL statement the APIs run (IF-CH-SCHEMA).

One function per API need; each returns models from :mod:`veyra_lineage.models`. API
handlers call these and never write SQL themselves.

All user input is bound server-side (``{name:Type}`` parameters), never formatted into
the SQL. Every function takes an optional ``client``; the default is the process-wide
client for ``VEYRA_CH_URL``/``VEYRA_CLICKHOUSE_DB``.

Replacing tables are read without ``FINAL``: ``LIMIT 1 BY`` / ``argMax`` pick one row per
key instead, which is cheaper and gives the same answer. Aggregate tables are always
re-aggregated with ``GROUP BY``, because their rows only collapse on merge.

Performance target: p95 < 50 ms per function on 1M rows (``tools/bench/ch_queries.py``).
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from clickhouse_connect.driver.client import Client

from veyra_common.models import RawRef, SourceMessage
from veyra_lineage.client import default_client
from veyra_lineage.models import (
    DlqSample,
    EventDetail,
    FieldCount,
    MatchKind,
    Overview,
    RawInfo,
    ReceiptRow,
    Revision,
    RouteHealth,
    RouteMinute,
    RouteStats,
    RouteStatus,
    SearchHit,
    SearchResult,
    ShadowRow,
    ShadowSummary,
    SourceHealth,
    SourceStatus,
    SourceStrip,
    TemplateEvent,
    TierTotals,
    TierTransition,
    VaultLocation,
    VaultStatus,
    WindowRootInfo,
)

OVERVIEW_WINDOW = timedelta(minutes=15)
HEALTH_LOOKBACK = timedelta(hours=24)
DEFAULT_SINCE = timedelta(hours=1)
MAX_LIMIT = 1000

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_SIG_RE = re.compile(r"^t_[0-9a-f]{12}$")
_SHA_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{8,64}$")

# `tenant` '' means every tenant (platform users are bound to '*').
_TENANT = "({tenant:String} = '' OR tenant_id = {tenant:String})"


def _ts(col: str) -> str:
    """SQL for a DateTime64(9) column as RFC3339 with nanoseconds, like the records."""
    return f"concat(replaceOne(toString({col}), ' ', 'T'), 'Z')"


# ---------------------------------------------------------------- helpers
def _client(client: Client | None) -> Client:
    return client or default_client()


# Hashes are FixedString(64); clickhouse-connect returns those as bytes by default.
_FORMATS = {"FixedString": "string"}


def _rows(client: Client, sql: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(client.query(sql, parameters=dict(params), query_formats=_FORMATS).named_results())


# A query function that needs several independent statements runs them at once: a
# session-less client is safe to share across threads, and the endpoint's latency is then
# its slowest statement rather than their sum.
_POOL = ThreadPoolExecutor(
    max_workers=min(8, (os.cpu_count() or 4) * 2), thread_name_prefix="veyra-lineage"
)


def _parallel(
    client: Client, statements: Mapping[str, tuple[str, Mapping[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    """Run every ``{name: (sql, params)}`` concurrently; return ``{name: rows}``."""
    futures = {
        name: _POOL.submit(_rows, client, sql, params) for name, (sql, params) in statements.items()
    }
    return {name: future.result() for name, future in futures.items()}


def _tenant(tenant: str | None) -> str:
    return "" if tenant in (None, "", "*") else str(tenant)


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _none(value: str | None) -> str | None:
    return value or None


def _dt(value: datetime | None) -> datetime | None:
    """ClickHouse's zero date (no data) -> None; always timezone-aware UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return None if value.year <= 1970 else value


def _since_ns(since: datetime | timedelta | None, default: timedelta) -> tuple[datetime, int]:
    """``since`` as (aware datetime, epoch ns). A timedelta means "that long ago"."""
    if since is None:
        since = default
    if isinstance(since, timedelta):
        since = datetime.now(UTC) - since
    elif since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return since, int(since.timestamp() * 1_000_000_000)


def _route_health(delivered: int, failed: int, filtered: int) -> RouteHealth:
    if delivered + failed + filtered == 0:
        return "idle"
    if failed == 0:
        return "ok"
    return "failing" if failed >= delivered else "degraded"


def _tiers(row: Mapping[str, Any]) -> TierTotals:
    return TierTotals(
        tier1=int(row["tier1"]),
        tier2=int(row["tier2"]),
        tier3=int(row["tier3"]),
        tier4=int(row["tier4"]),
    )


def classify(q: str) -> MatchKind:
    """How :func:`search` interprets ``q``."""
    if _UUID_RE.match(q):
        return "event_uid"
    if _SIG_RE.match(q):
        return "template_sig"
    if _SHA_PREFIX_RE.match(q):
        return "sha256_prefix"
    return "search_terms"


# ---------------------------------------------------------------- overview
_SQL_EPS_1M = """
SELECT tenant_id, source_id, count() AS n
FROM raw_events
WHERE {tenant_cond} AND received_time >= now64(9) - toIntervalSecond(60)
GROUP BY tenant_id, source_id
""".replace("{tenant_cond}", _TENANT)

_SQL_SOURCE_STRIP = """
SELECT tenant_id, source_id,
       sum(raw_count) AS raw, sum(raw_bytes) AS bytes, max(last_received) AS last_seen,
       sum(tier1) AS tier1, sum(tier2) AS tier2, sum(tier3) AS tier3, sum(tier4) AS tier4
FROM mv_source_minute
WHERE {tenant_cond}
  AND minute >= toStartOfMinute(fromUnixTimestamp64Nano({since:Int64}, 'UTC'))
GROUP BY tenant_id, source_id
ORDER BY raw DESC, source_id
""".replace("{tenant_cond}", _TENANT)

_SQL_ROUTE_TOTALS = """
SELECT route_id, sum(delivered) AS delivered, sum(failed) AS failed,
       sum(filtered) AS filtered, max(last_at) AS last_at
FROM mv_route_minute
WHERE minute >= toStartOfMinute(fromUnixTimestamp64Nano({since:Int64}, 'UTC'))
GROUP BY route_id
ORDER BY route_id
"""

_SQL_VAULT_SEGMENTS = """
SELECT uniqExact(segment_id) AS segments, max(sealed_at) AS last_sealed_at
FROM segments
"""

_SQL_LAST_ROOT = """
SELECT window_id, window_end, leaf_count, root, immudb_verified
FROM window_roots
ORDER BY window_end DESC, inserted_at DESC
LIMIT 1
"""


def overview(tenant: str | None = None, *, client: Client | None = None) -> Overview:
    """EPS over the last 60 s, tier totals and per-source strip over the last 15 min,
    route status, vault status."""
    ch = _client(client)
    t = _tenant(tenant)
    _, since = _since_ns(OVERVIEW_WINDOW, OVERVIEW_WINDOW)

    got = _parallel(
        ch,
        {
            "eps": (_SQL_EPS_1M, {"tenant": t}),
            "strip": (_SQL_SOURCE_STRIP, {"tenant": t, "since": since}),
            "routes": (_SQL_ROUTE_TOTALS, {"since": since}),
            "vault": (_SQL_VAULT_SEGMENTS, {}),
            "root": (_SQL_LAST_ROOT, {}),
        },
    )
    eps = {(r["tenant_id"], r["source_id"]): int(r["n"]) for r in got["eps"]}
    sources: list[SourceStrip] = []
    totals = TierTotals()
    for r in got["strip"]:
        tiers = _tiers(r)
        totals = TierTotals(
            tier1=totals.tier1 + tiers.tier1,
            tier2=totals.tier2 + tiers.tier2,
            tier3=totals.tier3 + tiers.tier3,
            tier4=totals.tier4 + tiers.tier4,
        )
        sources.append(
            SourceStrip(
                tenant_id=r["tenant_id"],
                source_id=r["source_id"],
                eps_1m=eps.get((r["tenant_id"], r["source_id"]), 0) / 60,
                raw_15m=int(r["raw"]),
                bytes_15m=int(r["bytes"]),
                tiers=tiers,
                last_seen=_dt(r["last_seen"]),
            )
        )

    routes = [
        RouteStatus(
            route_id=r["route_id"],
            delivered=int(r["delivered"]),
            failed=int(r["failed"]),
            filtered=int(r["filtered"]),
            last_at=_dt(r["last_at"]),
            status=_route_health(int(r["delivered"]), int(r["failed"]), int(r["filtered"])),
        )
        for r in got["routes"]
    ]

    v = got["vault"][0]
    last_root = None
    for r in got["root"]:
        last_root = WindowRootInfo(
            window_id=r["window_id"],
            window_end=_dt(r["window_end"]) or datetime.fromtimestamp(0, UTC),
            leaf_count=int(r["leaf_count"]),
            root=r["root"],
            immudb_verified=bool(r["immudb_verified"]),
        )
    return Overview(
        tenant=t or None,
        generated_at=datetime.now(UTC),
        eps_1m=sum(eps.values()) / 60,
        totals_by_tier=totals,
        sources=sources,
        routes=routes,
        vault=VaultStatus(
            segments=int(v["segments"]),
            last_sealed_at=_dt(v["last_sealed_at"]),
            last_root=last_root,
        ),
    )


# ---------------------------------------------------------------- source health
_SQL_SOURCE_HEALTH = """
WITH toStartOfMinute(now()) AS this_minute,
     toStartOfMinute(fromUnixTimestamp64Nano({mix_since:Int64}, 'UTC')) AS mix_from
SELECT tenant_id, source_id,
       sumIf(raw_count, minute >= this_minute - toIntervalSecond({window_s:UInt32})
                        AND minute < this_minute) AS raw_window,
       max(last_received) AS last_seen,
       sumIf(tier1, minute >= mix_from) AS tier1,
       sumIf(tier2, minute >= mix_from) AS tier2,
       sumIf(tier3, minute >= mix_from) AS tier3,
       sumIf(tier4, minute >= mix_from) AS tier4,
       argMaxMerge(last_contract_ref) AS contract_ref,
       quantileTDigestMergeIf(0.5)(clock_skew_ms, minute >= mix_from) AS skew_p50
FROM mv_source_minute
WHERE {tenant_cond}
  AND minute >= toStartOfMinute(fromUnixTimestamp64Nano({lookback:Int64}, 'UTC'))
GROUP BY tenant_id, source_id
ORDER BY tenant_id, source_id
""".replace("{tenant_cond}", _TENANT)


def source_health(
    tenant: str | None = None,
    expected: Mapping[str, float] | Iterable[SourceMessage] | None = None,
    *,
    window_s: int = 300,
    client: Client | None = None,
) -> list[SourceHealth]:
    """Expected vs actual EPS, last seen, tier mix, contract ref, clock skew p50.

    ``expected`` comes from control ``source:*`` (the caller has it): either the
    ``SourceMessage`` values or ``{source_id: expected_eps}``. Registered sources that sent
    nothing in the last 24 h still get a row (``silent``); sources sending without being
    registered are ``unexpected``. ``actual_eps`` is over the last ``window_s`` seconds of
    complete minutes, tier mix and skew over the last 15 minutes.
    """
    ch = _client(client)
    t = _tenant(tenant)
    window_s = max(60, int(window_s) // 60 * 60)
    _, mix_since = _since_ns(OVERVIEW_WINDOW, OVERVIEW_WINDOW)
    _, lookback = _since_ns(HEALTH_LOOKBACK, HEALTH_LOOKBACK)

    exp_eps: dict[str, float] | None = None
    exp_tenant: dict[str, str] = {}
    if expected is not None:
        exp_eps = {}
        if isinstance(expected, Mapping):
            exp_eps = {k: float(v) for k, v in expected.items()}
        else:
            for src in cast("Iterable[SourceMessage]", expected):
                if t and src.tenant_id != t:
                    continue
                exp_eps[src.source_id] = src.expected_eps
                exp_tenant[src.source_id] = src.tenant_id

    out: list[SourceHealth] = []
    seen: set[str] = set()
    params = {"tenant": t, "window_s": window_s, "mix_since": mix_since, "lookback": lookback}
    for r in _rows(ch, _SQL_SOURCE_HEALTH, params):
        source_id = r["source_id"]
        seen.add(source_id)
        actual = int(r["raw_window"]) / window_s
        last_seen = _dt(r["last_seen"])
        want = None if exp_eps is None else exp_eps.get(source_id)
        status: SourceStatus
        if exp_eps is not None and want is None:
            status = "unexpected"
        elif actual == 0:
            status = "silent"
        elif want and actual < 0.5 * want:
            status = "low"
        else:
            status = "ok"
        skew = r["skew_p50"]
        out.append(
            SourceHealth(
                tenant_id=r["tenant_id"],
                source_id=source_id,
                expected_eps=want,
                actual_eps=actual,
                last_seen=last_seen,
                tier_mix=_tiers(r),
                contract_ref=_none(r["contract_ref"]),
                clock_skew_p50_ms=None if skew is None or math.isnan(skew) else float(skew),
                status=status,
            )
        )
    for source_id, want in (exp_eps or {}).items():
        if source_id not in seen:
            out.append(
                SourceHealth(
                    tenant_id=exp_tenant.get(source_id, t),
                    source_id=source_id,
                    expected_eps=want,
                    actual_eps=0.0,
                    tier_mix=TierTotals(),
                    status="silent",
                )
            )
    return out


# ---------------------------------------------------------------- search
# search_index (migration 003) is ordered by (kind, key, event_uid): a term, signature
# or SHA prefix is a primary-key range, read newest-first so LIMIT stops early. Rows
# are per revision, so fetch a few more than needed and keep the first per event.
_INDEX_KEY = {
    "search_terms": "kind = 'term' AND key = {q:String}",
    "template_sig": "kind = 'sig' AND key = {q:String}",
    "sha256_prefix": "kind = 'sha' AND key LIKE {prefix:String}",
}
_REVISIONS_PER_EVENT = 4


def _index_sql(kind: MatchKind) -> str:
    return f"""
SELECT event_uid
FROM search_index
WHERE {_INDEX_KEY[kind]} AND {_TENANT}
  AND ({{source:String}} = '' OR source_id = {{source:String}})
ORDER BY event_uid DESC
LIMIT {{fetch:UInt32}}
"""


_SQL_LINEAGE_FOR_UIDS = f"""
SELECT event_uid, tenant_id, source_id, revision, tier, conformance, template_sig,
       contract_ref, raw_sha256, raw_topic, raw_partition, raw_offset,
       {_ts("produced_at")} AS produced_at
FROM norm_lineage
WHERE event_uid IN {{uids:Array(String)}}
ORDER BY event_uid DESC, revision DESC, indexed_at DESC
LIMIT 1 BY event_uid
"""

_SQL_RAW_FOR_UIDS = f"""
SELECT event_uid, tenant_id, source_id, raw_sha256,
       {_ts("received_time")} AS received_time, raw_preview
FROM raw_events
WHERE event_uid IN {{uids:Array(String)}}
LIMIT 1 BY event_uid
"""


def _index_uids(
    ch: Client, kind: MatchKind, q: str, tenant: str, limit: int, source: str = ""
) -> list[str]:
    params = {
        "q": q,
        "prefix": q.lower() + "%",
        "tenant": tenant,
        "source": source,
        "fetch": limit * _REVISIONS_PER_EVENT,
    }
    uids = dict.fromkeys(r["event_uid"] for r in _rows(ch, _index_sql(kind), params))
    return list(uids)[:limit]


def search(
    q: str, tenant: str | None = None, limit: int = 50, *, client: Client | None = None
) -> SearchResult:
    """Find events by ``event_uid`` (exact), SHA-256 prefix (≥ 8 hex), template_sig
    (``t_<12 hex>``), or any search term (IP, user, hostname) — ``has(search_terms, q)``,
    answered from ``search_index``.

    One hit per event (its latest revision), newest first, with the raw preview. Events
    indexed from ``raw.*`` but not normalized yet are found by uid and SHA. A hex string
    that matches no SHA falls back to a search-term lookup.
    """
    ch = _client(client)
    q = q.strip()
    if not q:
        return SearchResult(q=q, matched_on=None, hits=[])
    t = _tenant(tenant)
    n = _limit(limit)
    kind = classify(q)
    hits = _search(ch, kind, q, t, n)
    if not hits and kind == "sha256_prefix":
        kind = "search_terms"
        hits = _search(ch, kind, q, t, n)
    return SearchResult(q=q, matched_on=kind if hits else None, hits=hits)


def _search(ch: Client, kind: MatchKind, q: str, tenant: str, limit: int) -> list[SearchHit]:
    uids = [q.lower()] if kind == "event_uid" else _index_uids(ch, kind, q, tenant, limit)
    if not uids:
        return []
    got = _parallel(
        ch,
        {
            "lineage": (_SQL_LINEAGE_FOR_UIDS, {"uids": uids}),
            "raw": (_SQL_RAW_FOR_UIDS, {"uids": uids}),
        },
    )
    lineage = {r["event_uid"]: r for r in got["lineage"]}
    raw = {r["event_uid"]: r for r in got["raw"]}
    hits: list[SearchHit] = []
    for uid in uids:
        lin, rw = lineage.get(uid), raw.get(uid)
        base = lin or rw
        if base is None or (tenant and base["tenant_id"] != tenant):
            continue
        hits.append(
            SearchHit(
                event_uid=uid,
                tenant_id=base["tenant_id"],
                source_id=base["source_id"],
                received_time=rw["received_time"] if rw else None,
                revision=int(lin["revision"]) if lin else None,
                tier=int(lin["tier"]) if lin else None,
                conformance=lin["conformance"] if lin else None,
                template_sig=lin["template_sig"] if lin else None,
                contract_ref=_none(lin["contract_ref"]) if lin else None,
                raw_sha256=base["raw_sha256"],
                raw_preview=rw["raw_preview"] if rw else None,
            )
        )
    hits.sort(key=lambda h: h.event_uid, reverse=True)
    return hits


# ---------------------------------------------------------------- event detail
_SQL_DETAIL_LINEAGE = f"""
SELECT revision, tier, conformance, contract_ref, template_sig, template_id, class_uid,
       category, norm_topic, {_ts("produced_at")} AS produced_at, replay, replay_job_id,
       search_terms, raw_topic, raw_partition, raw_offset
FROM norm_lineage
WHERE event_uid = {{uid:String}}
ORDER BY revision, indexed_at DESC
LIMIT 1 BY revision
"""

_SQL_DETAIL_NORM = """
SELECT revision, ocsf_json
FROM norm_events
WHERE event_uid = {uid:String}
ORDER BY revision, indexed_at DESC
LIMIT 1 BY revision
"""

_SQL_DETAIL_RAW = f"""
SELECT raw_topic, raw_partition, raw_offset, raw_sha256, raw_len,
       {_ts("received_time")} AS received_time, tenant_id, source_id, vendor, zone,
       collector_id, transport, listener, peer_ip, custody, auth_method, framing_method,
       framing_truncated, framing_parts, raw_preview
FROM raw_events
WHERE event_uid = {{uid:String}}
LIMIT 1
"""

_SQL_DETAIL_VAULT = f"""
SELECT segment_id, record_idx, chain_hash, sealed, {_ts("sealed_at")} AS sealed_at,
       raw_topic, raw_partition, raw_offset,
       (SELECT argMax(window_id, version) FROM segments
        WHERE segment_id IN (SELECT segment_id FROM vault_locations
                             WHERE event_uid = {{uid:String}})) AS window_id
FROM vault_locations
WHERE event_uid = {{uid:String}}
ORDER BY sealed_at DESC
LIMIT 1
"""

_SQL_DETAIL_RECEIPTS = f"""
SELECT revision, route_id, status, detail, {_ts("at")} AS at
FROM receipts
WHERE event_uid = {{uid:String}}
ORDER BY at, route_id
LIMIT 1 BY revision, route_id, status, at
"""

_DLQ_COLS = f"""
    event_uid, tenant_id, source_id, tier, reason_code, reason_detail, contract_ref,
    template_sig, text_masked, parse_path, {_ts("produced_at")} AS produced_at
"""

_SQL_DETAIL_DLQ = f"""
SELECT {_DLQ_COLS}
FROM dlq_events
WHERE event_uid = {{uid:String}}
ORDER BY produced_at
"""

_SQL_DETAIL_SHADOW = f"""
SELECT contract_id, active_ref, candidate_ref, active_tier, candidate_tier,
       changed_fields, regressions, {_ts("produced_at")} AS produced_at
FROM shadow_diffs
WHERE event_uid = {{uid:String}}
ORDER BY produced_at
"""


def _dlq(r: Mapping[str, Any]) -> DlqSample:
    return DlqSample(
        event_uid=r["event_uid"],
        tenant_id=r["tenant_id"],
        source_id=r["source_id"],
        tier=int(r["tier"]),
        reason_code=r["reason_code"],
        reason_detail=r["reason_detail"],
        contract_ref=_none(r["contract_ref"]),
        template_sig=r["template_sig"],
        text_masked=r["text_masked"],
        parse_path=list(r["parse_path"]),
        produced_at=r["produced_at"],
    )


def event_detail(event_uid: str, *, client: Client | None = None) -> EventDetail | None:
    """One event's full lineage: the raw reference and envelope metadata, every revision
    (IF-LINEAGE row + the IF-NORM-EVENT body), vault location, receipts, DLQ and shadow
    records. ``None`` if the index has never seen the event."""
    ch = _client(client)
    p = {"uid": event_uid.strip()}
    got = _parallel(
        ch,
        {
            "lineage": (_SQL_DETAIL_LINEAGE, p),
            "norm": (_SQL_DETAIL_NORM, p),
            "raw": (_SQL_DETAIL_RAW, p),
            "vault": (_SQL_DETAIL_VAULT, p),
            "receipts": (_SQL_DETAIL_RECEIPTS, p),
            "dlq": (_SQL_DETAIL_DLQ, p),
            "shadow": (_SQL_DETAIL_SHADOW, p),
        },
    )
    lineage = got["lineage"]
    norm = {int(r["revision"]): r["ocsf_json"] for r in got["norm"]}
    raw_rows, vault_rows = got["raw"], got["vault"]
    receipts, dlq, shadow = got["receipts"], got["dlq"], got["shadow"]
    if not (lineage or norm or raw_rows or vault_rows or receipts or dlq or shadow):
        return None

    raw = None
    raw_ref = None
    if raw_rows:
        r = raw_rows[0]
        raw_ref = RawRef(
            topic=r["raw_topic"], partition=int(r["raw_partition"]), offset=int(r["raw_offset"])
        )
        raw = RawInfo(
            raw_ref=raw_ref,
            raw_sha256=r["raw_sha256"],
            raw_len=int(r["raw_len"]),
            received_time=r["received_time"],
            tenant_id=r["tenant_id"],
            source_id=r["source_id"],
            vendor=r["vendor"],
            zone=r["zone"],
            collector_id=r["collector_id"],
            transport=r["transport"],
            listener=_none(r["listener"]),
            peer_ip=_none(r["peer_ip"]),
            custody=r["custody"],
            auth_method=r["auth_method"],
            framing_method=r["framing_method"],
            framing_truncated=bool(r["framing_truncated"]),
            framing_parts=int(r["framing_parts"]),
            raw_preview=r["raw_preview"],
        )
    elif lineage:
        r = lineage[0]
        raw_ref = RawRef(
            topic=r["raw_topic"], partition=int(r["raw_partition"]), offset=int(r["raw_offset"])
        )
    elif vault_rows:
        r = vault_rows[0]
        raw_ref = RawRef(
            topic=r["raw_topic"], partition=int(r["raw_partition"]), offset=int(r["raw_offset"])
        )

    revisions = [
        Revision(
            revision=int(r["revision"]),
            tier=int(r["tier"]),
            conformance=r["conformance"],
            contract_ref=_none(r["contract_ref"]),
            template_sig=r["template_sig"],
            template_id=_none(r["template_id"]),
            class_uid=int(r["class_uid"]),
            category=r["category"],
            norm_topic=r["norm_topic"],
            produced_at=r["produced_at"],
            replay=bool(r["replay"]),
            replay_job_id=_none(r["replay_job_id"]),
            search_terms=list(r["search_terms"]),
            ocsf=json.loads(norm[int(r["revision"])]) if int(r["revision"]) in norm else None,
        )
        for r in lineage
    ]

    vault = None
    if vault_rows:
        r = vault_rows[0]
        vault = VaultLocation(
            segment_id=r["segment_id"],
            record_idx=int(r["record_idx"]),
            chain_hash=r["chain_hash"],
            sealed=bool(r["sealed"]),
            sealed_at=r["sealed_at"],
            window_id=_none(r["window_id"]),
        )

    return EventDetail(
        event_uid=p["uid"],
        raw_ref=raw_ref,
        raw=raw,
        revisions=revisions,
        vault=vault,
        receipts=[
            ReceiptRow(
                revision=int(r["revision"]),
                route_id=r["route_id"],
                status=r["status"],
                detail=r["detail"],
                at=r["at"],
            )
            for r in receipts
        ],
        dlq=[_dlq(r) for r in dlq],
        shadow=[
            ShadowRow(
                contract_id=r["contract_id"],
                active_ref=_none(r["active_ref"]),
                candidate_ref=r["candidate_ref"],
                active_tier=int(r["active_tier"]),
                candidate_tier=int(r["candidate_tier"]),
                changed_fields=list(r["changed_fields"]),
                regressions=list(r["regressions"]),
                produced_at=r["produced_at"],
            )
            for r in shadow
        ],
    )


# ---------------------------------------------------------------- templates / dlq
def template_events(
    sig: str, source: str | None = None, limit: int = 100, *, client: Client | None = None
) -> list[TemplateEvent]:
    """Events of one template (newest first), with the raw_ref replay needs.
    ``revision``/``tier`` are the event's latest revision."""
    ch = _client(client)
    uids = _index_uids(ch, "template_sig", sig, "", _limit(limit), source or "")
    if not uids:
        return []
    return [
        TemplateEvent(
            event_uid=r["event_uid"],
            tenant_id=r["tenant_id"],
            source_id=r["source_id"],
            revision=int(r["revision"]),
            tier=int(r["tier"]),
            raw_ref=RawRef(
                topic=r["raw_topic"],
                partition=int(r["raw_partition"]),
                offset=int(r["raw_offset"]),
            ),
            raw_sha256=r["raw_sha256"],
            produced_at=r["produced_at"],
        )
        for r in _rows(ch, _SQL_LINEAGE_FOR_UIDS, {"uids": uids})
    ]


_SQL_DLQ_SAMPLES = f"""
SELECT {_DLQ_COLS}
FROM dlq_events
WHERE ({{source:String}} = '' OR source_id = {{source:String}})
  AND ({{sig:String}} = '' OR template_sig = {{sig:String}})
ORDER BY produced_at DESC
LIMIT {{limit:UInt32}}
"""


def dlq_samples(
    source: str | None,
    sig: str | None = None,
    limit: int = 20,
    *,
    client: Client | None = None,
) -> list[DlqSample]:
    """Most recent DLQ records for a source (and template), PII-masked text included."""
    rows = _rows(
        _client(client),
        _SQL_DLQ_SAMPLES,
        {"source": source or "", "sig": sig or "", "limit": _limit(limit)},
    )
    return [_dlq(r) for r in rows]


# ---------------------------------------------------------------- shadow
_SHADOW_WHERE = (
    "contract_id = {contract:String} "
    "AND produced_at >= fromUnixTimestamp64Nano({since:Int64}, 'UTC')"
)

_SQL_SHADOW_TOTALS = f"""
SELECT candidate_ref,
       argMax(active_ref, produced_at) AS active_ref,
       count() AS total,
       uniqExact(event_uid) AS events,
       countIf(candidate_tier < active_tier) AS improved,
       countIf(candidate_tier = active_tier) AS unchanged,
       countIf(candidate_tier > active_tier) AS worsened,
       countIf(notEmpty(regressions)) AS with_regressions,
       min(produced_at) AS first_at,
       max(produced_at) AS last_at,
       groupArrayIf(10)(event_uid, notEmpty(regressions)) AS regression_samples
FROM shadow_diffs
WHERE {_SHADOW_WHERE}
GROUP BY candidate_ref
ORDER BY last_at DESC
"""

_SQL_SHADOW_TRANSITIONS = f"""
SELECT candidate_ref, active_tier, candidate_tier, count() AS n
FROM shadow_diffs
WHERE {_SHADOW_WHERE}
GROUP BY candidate_ref, active_tier, candidate_tier
ORDER BY candidate_ref, active_tier, candidate_tier
"""

_SQL_SHADOW_FIELDS = f"""
SELECT candidate_ref, 'changed' AS kind, field, count() AS n
FROM shadow_diffs ARRAY JOIN changed_fields AS field
WHERE {_SHADOW_WHERE}
GROUP BY candidate_ref, field
ORDER BY n DESC, field
LIMIT 10 BY candidate_ref
UNION ALL
SELECT candidate_ref, 'regression' AS kind, field, count() AS n
FROM shadow_diffs ARRAY JOIN regressions AS field
WHERE {_SHADOW_WHERE}
GROUP BY candidate_ref, field
ORDER BY n DESC, field
LIMIT 10 BY candidate_ref
"""


def shadow_summary(
    contract_id: str,
    since: datetime | timedelta | None = None,
    *,
    client: Client | None = None,
) -> list[ShadowSummary]:
    """Candidate-vs-active outcome per candidate version since ``since`` (default: 1 h),
    most recently active candidate first."""
    ch = _client(client)
    since_dt, since_ns = _since_ns(since, DEFAULT_SINCE)
    p = {"contract": contract_id, "since": since_ns}
    got = _parallel(
        ch,
        {
            "totals": (_SQL_SHADOW_TOTALS, p),
            "transitions": (_SQL_SHADOW_TRANSITIONS, p),
            "fields": (_SQL_SHADOW_FIELDS, p),
        },
    )
    transitions: dict[str, list[TierTransition]] = {}
    for r in got["transitions"]:
        transitions.setdefault(r["candidate_ref"], []).append(
            TierTransition(
                active_tier=int(r["active_tier"]),
                candidate_tier=int(r["candidate_tier"]),
                count=int(r["n"]),
            )
        )
    fields: dict[tuple[str, str], list[FieldCount]] = {}
    for r in got["fields"]:
        fields.setdefault((r["candidate_ref"], r["kind"]), []).append(
            FieldCount(field=r["field"], count=int(r["n"]))
        )
    by_count = lambda fc: (-fc.count, fc.field)  # noqa: E731 - UNION ALL loses the order
    return [
        ShadowSummary(
            contract_id=contract_id,
            candidate_ref=r["candidate_ref"],
            active_ref=_none(r["active_ref"]),
            since=since_dt,
            total=int(r["total"]),
            events=int(r["events"]),
            improved=int(r["improved"]),
            unchanged=int(r["unchanged"]),
            worsened=int(r["worsened"]),
            with_regressions=int(r["with_regressions"]),
            first_at=_dt(r["first_at"]),
            last_at=_dt(r["last_at"]),
            transitions=transitions.get(r["candidate_ref"], []),
            top_changed_fields=sorted(
                fields.get((r["candidate_ref"], "changed"), []), key=by_count
            ),
            top_regressions=sorted(
                fields.get((r["candidate_ref"], "regression"), []), key=by_count
            ),
            regression_samples=list(r["regression_samples"]),
        )
        for r in got["totals"]
    ]


# ---------------------------------------------------------------- routes
_SQL_ROUTE_STATS = """
SELECT route_id, minute, sum(delivered) AS delivered, sum(failed) AS failed,
       sum(filtered) AS filtered, max(last_at) AS last_at
FROM mv_route_minute
WHERE minute >= toStartOfMinute(fromUnixTimestamp64Nano({since:Int64}, 'UTC'))
GROUP BY route_id, minute
ORDER BY route_id, minute
"""


def route_stats(
    since: datetime | timedelta | None = None, *, client: Client | None = None
) -> list[RouteStats]:
    """Per-route delivered/failed/filtered since ``since`` (default: 1 h), with a
    per-minute series for the sparkline."""
    since_dt, since_ns = _since_ns(since, DEFAULT_SINCE)
    by_route: dict[str, list[dict[str, Any]]] = {}
    for r in _rows(_client(client), _SQL_ROUTE_STATS, {"since": since_ns}):
        by_route.setdefault(r["route_id"], []).append(r)
    out: list[RouteStats] = []
    for route_id, rows in by_route.items():
        delivered = sum(int(r["delivered"]) for r in rows)
        failed = sum(int(r["failed"]) for r in rows)
        filtered = sum(int(r["filtered"]) for r in rows)
        last = [d for d in (_dt(r["last_at"]) for r in rows) if d is not None]
        out.append(
            RouteStats(
                route_id=route_id,
                since=since_dt,
                delivered=delivered,
                failed=failed,
                filtered=filtered,
                last_at=max(last) if last else None,
                status=_route_health(delivered, failed, filtered),
                per_minute=[
                    RouteMinute(
                        minute=_dt(r["minute"]) or since_dt,
                        delivered=int(r["delivered"]),
                        failed=int(r["failed"]),
                        filtered=int(r["filtered"]),
                    )
                    for r in rows
                ],
            )
        )
    return out
