"""Seed ClickHouse with a synthetic lineage dataset (B1 task 4).

    python tools/seed_ch.py                       # 1M events into VEYRA_CLICKHOUSE_DB
    python tools/seed_ch.py --db veyra_bench --rows 1000000 --reset
    python tools/seed_ch.py --rows 20000 --hours 1   # small, for UI work

Rows are generated inside ClickHouse (``INSERT ... SELECT FROM numbers(N)``), so 1M events
take seconds and every materialized view fires exactly as it does for indexer inserts.
Data is deterministic for a given ``--seed``, spread over the last ``--hours`` and ending
now, so "last 15 minutes" queries have data.

Per event: 1 raw_events row, 1 norm_lineage + norm_events row (5 % get a replay
revision 2), 1 vault_locations row, 1-2 receipts; tier >= 2 events get a dlq_events row.
Plus segments (one per 1000 events), shadow_diffs, window_roots and audit_log.

Search fixtures baked in (AC4): tier-3 ``src_authsrv_01`` events carry ``103.21.4.77``
in ``search_terms`` for 1 in 20 of them, like the demo's failed-login burst.
"""

from __future__ import annotations

import argparse
import sys
import time

from veyra_common.settings import settings
from veyra_lineage.client import make_client
from veyra_lineage.migrate import migrate

# (tenant, source, vendor, zone, transport) — the demo world plus filler sources.
SOURCES: list[tuple[str, str, str, str, str]] = [
    ("t_maha_power", "src_authsrv_01", "custom", "dmz", "http_hec_event"),
    ("t_ntro_core", "src_lnx_core_07", "linux", "core", "syslog_udp"),
    ("t_ntro_core", "src_fw_dmz_01", "acme_ngfw", "dmz", "syslog_tcp"),
    ("unassigned", "unregistered", "unregistered", "core", "syslog_udp"),
]
for _i, _tenant in enumerate(("t_maha_power", "t_ntro_core", "t_city_water", "t_rail_ops")):
    for _j in range(5):
        SOURCES.append(
            (
                _tenant,
                f"src_{_tenant[2:]}_{_j:02d}",
                ("linux", "acme_ngfw", "custom")[(_i + _j) % 3],
                ("dmz", "core", "ot")[_j % 3],
                ("syslog_udp", "syslog_tcp", "http_hec_event")[_j % 3],
            )
        )

TEMPLATES_PER_SOURCE = 40
SEGMENT_SIZE = 1000
REPLAY_EVERY = 20  # 5 %
SHADOW_ROWS = 50_000
AUDIT_ROWS = 1_000
DEMO_IP = "103.21.4.77"


def _array(values: list[str]) -> str:
    return "[" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def event_cte(rows: int, hours: float, seed: int, end_ns: int) -> str:
    """Per-event columns every table's generator derives from (one row per event i)."""
    n_src = len(SOURCES)
    span_ns = int(hours * 3600 * 1_000_000_000)
    return f"""
WITH
    {_array([s[0] for s in SOURCES])} AS tenants,
    {_array([s[1] for s in SOURCES])} AS sources,
    {_array([s[2] for s in SOURCES])} AS vendors,
    {_array([s[3] for s in SOURCES])} AS zones,
    {_array([s[4] for s in SOURCES])} AS transports,
    number AS i,
    cityHash64(i, {seed}) AS h,
    toInt64({end_ns}) - toInt64({span_ns // rows}) * ({rows} - i) AS ts_ns,
    fromUnixTimestamp64Nano(ts_ns, 'UTC') AS ts,
    -- source 0 (authsrv) gets 3x the traffic, like the demo's noisy source
    if(h % 10 < 3, 1, 2 + (h % {n_src - 1})) AS sidx,
    tenants[sidx] AS tenant_id,
    sources[sidx] AS source_id,
    vendors[sidx] AS vendor,
    multiIf(bitShiftRight(h, 8) % 100 < 70, 1,
            bitShiftRight(h, 8) % 100 < 80, 2,
            bitShiftRight(h, 8) % 100 < 95, 3, 4) AS tier,
    ['match', 'partial', 'unknown_template', 'unparseable'][tier] AS conformance,
    bitShiftRight(h, 16) % {TEMPLATES_PER_SOURCE} AS tidx,
    concat('t_',
           substring(lower(hex(SHA256(concat(source_id, '#', toString(tidx))))), 1, 12))
        AS template_sig,
    leftPad(lower(hex(intDiv(ts_ns, 1000000))), 12, '0') AS tsh,
    concat(leftPad(lower(hex(cityHash64(i, {seed}, 1))), 16, '0'),
           leftPad(lower(hex(cityHash64(i, {seed}, 2))), 16, '0')) AS rh,
    concat(substring(tsh, 1, 8), '-', substring(tsh, 9, 4), '-7', substring(rh, 1, 3), '-',
           '8', substring(rh, 4, 3), '-', substring(rh, 7, 12)) AS event_uid,
    lower(hex(SHA256(concat('raw#', toString({seed}), '#', toString(i))))) AS raw_sha256,
    concat('raw.', vendor) AS raw_topic,
    toUInt16(h % 3) AS raw_partition,
    toUInt64(intDiv(i, 3)) AS raw_offset,
    concat('10.', toString(bitShiftRight(h, 24) % 256),
           '.', toString(bitShiftRight(h, 32) % 256),
           '.', toString(bitShiftRight(h, 40) % 256)) AS src_ip,
    concat('user', toString(bitShiftRight(h, 20) % 5000)) AS user_name,
    concat('host-', toString(bitShiftRight(h, 28) % 200)) AS host,
    source_id = 'src_authsrv_01' AND tier = 3 AND bitShiftRight(h, 48) % 20 = 0 AS demo_hit,
    if(demo_hit, '{DEMO_IP}', src_ip) AS peer_seen,
    concat('<134>', formatDateTime(ts, '%b %e %H:%i:%S'), ' ', host, ' app[233]: user=',
           user_name, if(tier = 1, ' OK', ' FAILED'), ' login from ', peer_seen,
           ' via 10.2.3.4 attempts:', toString(1 + h % 5)) AS preview,
    50 + (h % 400) AS raw_len,
    if(sidx = 4, '', concat(splitByChar('_', source_id)[2], '@', toString(1 + h % 3)))
        AS contract_ref,
    zones[sidx] AS zone,
    transports[sidx] AS transport
SELECT i, h, ts_ns, ts, sidx, tenant_id, source_id, vendor, zone, transport, tier,
       conformance, tidx, template_sig, event_uid, raw_sha256, raw_topic, raw_partition,
       raw_offset, src_ip, user_name, host, demo_hit, peer_seen, preview, raw_len,
       contract_ref
FROM numbers({rows})
"""


def statements(db: str, rows: int, hours: float, seed: int, end_ns: int) -> list[tuple[str, str]]:
    ev = event_cte(rows, hours, seed, end_ns)
    k = "toUInt16(0), toUInt64(i), fromUnixTimestamp64Milli(intDiv(ts_ns, 1000000), 'UTC')"
    return [
        (
            "raw_events",
            f"""INSERT INTO {db}.raw_events
            (event_uid, tenant_id, source_id, vendor, zone, collector_id, transport, listener,
             peer_ip, peer_port, custody, auth_method, auth_key_id, received_time, seq_no,
             raw_topic, raw_partition, raw_offset, kafka_ts, raw_sha256, raw_len,
             framing_method, framing_truncated, framing_parts, salt, raw_preview)
            SELECT event_uid, tenant_id, source_id, vendor, zone, 'edge-seed-01',
                   transport, 'seed', peer_seen, 40000 + h % 20000, 'realtime',
                   'ip_map', '', ts, NULL, raw_topic, raw_partition, raw_offset,
                   fromUnixTimestamp64Milli(intDiv(ts_ns, 1000000), 'UTC'), raw_sha256,
                   raw_len, 'newline', false, 1, NULL, substring(preview, 1, 256)
            FROM ({ev})""",
        ),
        (
            "norm_lineage",
            f"""INSERT INTO {db}.norm_lineage
            (event_uid, revision, tenant_id, source_id, raw_topic, raw_partition, raw_offset,
             raw_sha256, contract_ref, template_sig, template_id, tier, conformance, class_uid,
             category, norm_topic, produced_at, replay, replay_job_id, search_terms,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, rev, tenant_id, source_id, raw_topic, raw_partition, raw_offset,
                   raw_sha256, contract_ref, template_sig,
                   if(rev_tier <= 2, concat('tpl_', toString(tidx)), ''), rev_tier,
                   ['match', 'partial', 'unknown_template', 'unparseable'][rev_tier],
                   if(rev_tier <= 2, 3002, 0), if(rev_tier <= 2, 'iam', 'uncategorized'),
                   if(rev_tier <= 2, 'norm.iam', 'norm.uncategorized'),
                   ts + toIntervalMillisecond(40 + (rev - 1) * 600000), rev = 2,
                   if(rev = 2, 'job_seed', ''),
                   arrayDistinct([peer_seen, '10.2.3.4', user_name, host]),
                   'lineage', toUInt16(0), toUInt64(i * 2 + rev - 1),
                   fromUnixTimestamp64Milli(intDiv(ts_ns, 1000000), 'UTC')
            FROM ({ev})
            ARRAY JOIN if(i % {REPLAY_EVERY} = 0, [1, 2], [1]) AS rev
            ARRAY JOIN [if(rev = 2, 1, tier)] AS rev_tier""",
        ),
        (
            "norm_events",
            f"""INSERT INTO {db}.norm_events
            (event_uid, revision, tenant_id, source_id, class_uid, tier, received_time,
             clock_skew_ms, ocsf_json, kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, 1, tenant_id, source_id, if(tier <= 2, 3002, 0), tier, ts,
                   toInt64(h % 2000) - 1000,
                   concat('{{"class_uid":', if(tier <= 2, '3002', '0'),
                          ',"message":"', preview, '","ulpf":{{"event_uid":"', event_uid,
                          '","tier":', toString(tier), ',"revision":1}}}}'),
                   'norm.seed', {k}
            FROM ({ev})""",
        ),
        (
            "vault_locations",
            f"""INSERT INTO {db}.vault_locations
            (event_uid, raw_topic, raw_partition, raw_offset, segment_id, record_idx,
             chain_hash, sealed, sealed_at, kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, raw_topic, raw_partition, raw_offset,
                   concat('seg_seed_0_',
                          leftPad(toString(intDiv(i, {SEGMENT_SIZE}) * {SEGMENT_SIZE}), 12, '0')),
                   i % {SEGMENT_SIZE}, lower(hex(SHA256(concat('chain#', toString(i))))), true,
                   ts + toIntervalSecond(20), 'vault_index', {k}
            FROM ({ev})""",
        ),
        (
            "segments",
            f"""INSERT INTO {db}.segments
            (segment_id, topic, partition, first_offset, last_offset, record_count,
             prev_chain_hash, last_chain_hash, digest, sealed_at, window_id,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT concat('seg_seed_0_', leftPad(toString(i), 12, '0')), 'raw.seed', 0, i,
                   i + {SEGMENT_SIZE - 1}, {SEGMENT_SIZE},
                   lower(hex(SHA256(concat('chain#', toString(i - 1))))),
                   lower(hex(SHA256(concat('chain#', toString(i + {SEGMENT_SIZE - 1}))))),
                   lower(hex(SHA256(concat('seg#', toString(i))))),
                   ts + toIntervalSecond(20),
                   concat('w_', toString(
                       intDiv(toUnixTimestamp(ts + toIntervalSecond(20)), 60) * 60)),
                   'vault_index', {k}
            FROM ({ev}) WHERE i % {SEGMENT_SIZE} = 0""",
        ),
        (
            "receipts",
            f"""INSERT INTO {db}.receipts
            (event_uid, revision, route_id, status, detail, at,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, 1, route,
                   multiIf(bitShiftRight(h, 4) % 100 < 1, 'failed',
                           bitShiftRight(h, 4) % 100 < 2, 'filtered', 'delivered'),
                   '', ts + toIntervalMillisecond(90), 'receipts', {k}
            FROM ({ev})
            ARRAY JOIN if(tenant_id = 't_maha_power' AND tier <= 2,
                          ['wazuh_main', 'partner_masked'], ['wazuh_main']) AS route""",
        ),
        (
            "dlq_events",
            f"""INSERT INTO {db}.dlq_events
            (event_uid, tenant_id, source_id, tier, reason_code, reason_detail, contract_ref,
             template_sig, text_masked, parse_path, produced_at,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, tenant_id, source_id, tier,
                   ['required_missing', 'no_template_match', 'decode_error'][tier - 1],
                   'seeded', contract_ref, template_sig,
                   replaceRegexpOne(substring(preview, 1, 256),
                                    'user=\\\\S+', 'user=<USER_1>'),
                   ['syslog:rfc3164', 'auto:kv(rest)'], ts + toIntervalMillisecond(40),
                   'dlq', {k}
            FROM ({ev}) WHERE tier >= 2""",
        ),
        (
            "shadow_diffs",
            f"""INSERT INTO {db}.shadow_diffs
            (event_uid, contract_id, active_ref, candidate_ref, active_tier, candidate_tier,
             changed_fields, regressions, produced_at,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT event_uid, 'authsrv', 'authsrv@1', if(h % 4 = 0, 'authsrv@3', 'authsrv@2'),
                   tier, if(tier = 3, 1, tier),
                   if(tier = 3, ['user.name', 'src_endpoint.ip', 'status_id'], []),
                   if(h % 97 = 0, ['dst_endpoint.ip'], []),
                   ts + toIntervalMillisecond(45), 'shadow', {k}
            FROM ({ev}) WHERE source_id = 'src_authsrv_01'
            ORDER BY i DESC LIMIT {SHADOW_ROWS}""",
        ),
        (
            "audit_log",
            f"""INSERT INTO {db}.audit_log
            (actor, role, action, target, detail, at,
             kafka_topic, kafka_partition, kafka_offset, kafka_ts)
            SELECT ['author@maha', 'approver@veyra', 'admin@veyra'][1 + h % 3],
                   ['pack_author', 'pack_approver', 'admin'][1 + h % 3],
                   ['contract.submit', 'contract.approve',
                    'contract.promote', 'source.create'][1 + h % 4],
                   concat('authsrv@', toString(1 + h % 3)), 'seeded', ts, 'audit', {k}
            FROM ({ev}) WHERE i % {max(rows // AUDIT_ROWS, 1)} = 0""",
        ),
    ]


def window_roots_sql(db: str, hours: float, end_s: int) -> str:
    windows = int(hours * 60)
    return f"""INSERT INTO {db}.window_roots
        (window_id, window_start, window_end, leaf_count, root, prev_signed_sha256, sig_b64,
         key_id, immudb_tx, immudb_verified)
        SELECT concat('w_', toString(ws)), toDateTime(ws, 'UTC'), toDateTime(ws + 60, 'UTC'),
               3, lower(hex(SHA256(concat('root#', toString(ws))))),
               lower(hex(SHA256(concat('root#', toString(ws - 60))))), 'c2VlZA==',
               'veyra-root-ed25519-1', number + 1, true
        FROM (SELECT number, intDiv({end_s}, 60) * 60 - ({windows} - number) * 60 AS ws
              FROM numbers({windows}))"""


def seed(db: str, rows: int, hours: float, seed_value: int, *, reset: bool) -> dict[str, int]:
    admin = make_client(database="")
    if reset:
        admin.command(f"DROP DATABASE IF EXISTS {db} SYNC")
    migrate(client=admin, db=db)
    ch = make_client(database=db)
    end_ns = time.time_ns()
    settings_ = {"max_insert_threads": 4, "max_block_size": 65536}
    for table, sql in statements(db, rows, hours, seed_value, end_ns):
        started = time.monotonic()
        ch.command(sql, settings=settings_)
        print(f"  {table:<16} {time.monotonic() - started:6.2f}s", flush=True)
    ch.command(window_roots_sql(db, hours, end_ns // 1_000_000_000))
    ch.command(f"OPTIMIZE TABLE {db}.mv_source_minute FINAL")
    ch.command(f"OPTIMIZE TABLE {db}.mv_route_minute FINAL")
    counts = {}
    for table in (
        "raw_events",
        "norm_lineage",
        "norm_events",
        "vault_locations",
        "segments",
        "receipts",
        "dlq_events",
        "shadow_diffs",
        "audit_log",
        "window_roots",
        "mv_source_minute",
        "mv_route_minute",
    ):
        counts[table] = int(ch.query(f"SELECT count() FROM {db}.{table}").result_rows[0][0])
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--db", default=settings.clickhouse_db)
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--reset", action="store_true", help="drop the database first")
    args = parser.parse_args()
    if not args.db.replace("_", "").isalnum():
        raise SystemExit(f"bad database name: {args.db}")
    print(f"seeding {args.rows:,} events over {args.hours} h into {args.db}")
    started = time.monotonic()
    counts = seed(args.db, args.rows, args.hours, args.seed, reset=args.reset)
    for table, n in counts.items():
        print(f"  {table:<16} {n:>10,} rows")
    print(f"done in {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
