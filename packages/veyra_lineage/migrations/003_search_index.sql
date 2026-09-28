-- Inverted lookup for lineage search (B1, additive to IF-CH-SCHEMA).
--
-- norm_lineage is ordered by event_uid, so a search term, template signature or SHA
-- prefix is spread over every granule and no skip index can prune it (measured: 50-120 ms
-- p95 on 1M rows). search_index holds one row per (kind, key, event, revision), ordered
-- by key, so each lookup is a primary-key range read:
--   kind 'term' <- norm_lineage.search_terms      has(search_terms, q)
--   kind 'sig'  <- norm_lineage.template_sig      template_sig = q
--   kind 'sha'  <- raw_events.raw_sha256          raw_sha256 LIKE 'prefix%'
-- ReplacingMergeTree collapses any repeated (kind, key, event_uid, revision).

CREATE TABLE IF NOT EXISTS {db}.search_index
(
    kind               LowCardinality(String),
    key                String,
    event_uid          String,
    revision           UInt16,
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    at                 DateTime64(9, 'UTC')
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(at)
ORDER BY (kind, key, event_uid, revision)
TTL toDateTime(at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.search_index_from_lineage
TO {db}.search_index
AS SELECT
    kv.1 AS kind,
    kv.2 AS key,
    event_uid,
    revision,
    tenant_id,
    source_id,
    produced_at AS at
FROM {db}.norm_lineage
ARRAY JOIN arrayConcat(
    arrayMap(t -> ('term', t), arrayDistinct(search_terms)),
    [('sig', CAST(template_sig AS String))]
) AS kv;

CREATE MATERIALIZED VIEW IF NOT EXISTS {db}.search_index_from_raw
TO {db}.search_index
AS SELECT
    'sha' AS kind,
    CAST(raw_sha256 AS String) AS key,
    event_uid,
    toUInt16(0) AS revision,
    tenant_id,
    source_id,
    received_time AS at
FROM {db}.raw_events;

-- Backfill rows indexed before this migration (a no-op on a fresh database).
INSERT INTO {db}.search_index
SELECT kv.1, kv.2, event_uid, revision, tenant_id, source_id, produced_at
FROM {db}.norm_lineage
ARRAY JOIN arrayConcat(
    arrayMap(t -> ('term', t), arrayDistinct(search_terms)),
    [('sig', CAST(template_sig AS String))]
) AS kv;

INSERT INTO {db}.search_index
SELECT 'sha', CAST(raw_sha256 AS String), event_uid, 0, tenant_id, source_id, received_time
FROM {db}.raw_events;
