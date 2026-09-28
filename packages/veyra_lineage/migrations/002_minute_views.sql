-- IF-CH-SCHEMA aggregates (B1): mv_source_minute and mv_route_minute.
--
-- Each aggregate is an AggregatingMergeTree target table fed by one or more
-- materialized views (named mv_<target>_from_<source>). Queries read the target table
-- and GROUP BY its key, because rows only collapse on merge.
--
-- mv_source_minute gets its columns from three sources, keyed by (tenant, source, minute):
--   raw_events   -> raw_count, raw_bytes, last_received          (minute of received_time)
--   norm_lineage -> norm_count, tier1..tier4, replay_count,
--                   last_contract_ref                             (minute of produced_at)
--   norm_events  -> clock_skew_ms (t-digest state, ulpf.time)    (minute of received_time)
-- Columns a view does not produce take their default (0 / empty state), which is the
-- neutral element of the merge.

CREATE TABLE IF NOT EXISTS {db}.mv_source_minute
(
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    minute             DateTime('UTC'),
    raw_count          SimpleAggregateFunction(sum, UInt64),
    raw_bytes          SimpleAggregateFunction(sum, UInt64),
    last_received      SimpleAggregateFunction(max, DateTime64(9, 'UTC')),
    norm_count         SimpleAggregateFunction(sum, UInt64),
    tier1              SimpleAggregateFunction(sum, UInt64),
    tier2              SimpleAggregateFunction(sum, UInt64),
    tier3              SimpleAggregateFunction(sum, UInt64),
    tier4              SimpleAggregateFunction(sum, UInt64),
    replay_count       SimpleAggregateFunction(sum, UInt64),
    last_contract_ref  AggregateFunction(argMax, String, DateTime64(9, 'UTC')),
    clock_skew_ms      AggregateFunction(quantileTDigest(0.5), Int64)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (tenant_id, source_id, minute)
TTL minute + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_source_minute_from_raw
TO {db}.mv_source_minute
AS SELECT
    tenant_id,
    source_id,
    toStartOfMinute(received_time) AS minute,
    count() AS raw_count,
    sum(raw_len) AS raw_bytes,
    max(received_time) AS last_received
FROM {db}.raw_events
GROUP BY tenant_id, source_id, minute;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_source_minute_from_lineage
TO {db}.mv_source_minute
AS SELECT
    tenant_id,
    source_id,
    toStartOfMinute(produced_at) AS minute,
    count() AS norm_count,
    countIf(tier = 1) AS tier1,
    countIf(tier = 2) AS tier2,
    countIf(tier = 3) AS tier3,
    countIf(tier = 4) AS tier4,
    countIf(replay) AS replay_count,
    argMaxState(CAST(contract_ref AS String), produced_at) AS last_contract_ref
FROM {db}.norm_lineage
GROUP BY tenant_id, source_id, minute;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_source_minute_from_norm
TO {db}.mv_source_minute
AS SELECT
    tenant_id,
    source_id,
    toStartOfMinute(received_time) AS minute,
    quantileTDigestStateIf(0.5)(assumeNotNull(clock_skew_ms), isNotNull(clock_skew_ms))
        AS clock_skew_ms
FROM {db}.norm_events
GROUP BY tenant_id, source_id, minute;

CREATE TABLE IF NOT EXISTS {db}.mv_route_minute
(
    route_id           LowCardinality(String),
    minute             DateTime('UTC'),
    delivered          SimpleAggregateFunction(sum, UInt64),
    failed             SimpleAggregateFunction(sum, UInt64),
    filtered           SimpleAggregateFunction(sum, UInt64),
    last_at            SimpleAggregateFunction(max, DateTime64(9, 'UTC'))
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(minute)
ORDER BY (route_id, minute)
TTL minute + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.mv_route_minute_from_receipts
TO {db}.mv_route_minute
AS SELECT
    route_id,
    toStartOfMinute(at) AS minute,
    countIf(status = 'delivered') AS delivered,
    countIf(status = 'failed') AS failed,
    countIf(status = 'filtered') AS filtered,
    max(at) AS last_at
FROM {db}.receipts
GROUP BY route_id, minute;
