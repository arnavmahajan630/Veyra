-- IF-CH-SCHEMA event tables (B1). Applied once by veyra_lineage.migrate, recorded in
-- schema_migrations. Every statement is idempotent (IF NOT EXISTS) so a half-applied
-- file can simply be re-run.
--
-- Placeholders: {db} = VEYRA_CLICKHOUSE_DB, {ttl_days} = VEYRA_LINEAGE_TTL_DAYS.
-- A later change of VEYRA_LINEAGE_TTL_DAYS is applied by migrate.ensure_ttl().
--
-- Every indexed table carries the Kafka position of the record it came from
-- (kafka_topic/kafka_partition/kafka_offset/kafka_ts; raw_events reuses raw_ref for the
-- first three). The indexer uses them after a restart to skip records that were
-- inserted but whose offsets were never committed (at-least-once, no duplicates).
--
-- non_replicated_deduplication_window lets insert_deduplication_token work on these
-- non-replicated tables, so a retried INSERT of the same batch is a no-op.

CREATE TABLE IF NOT EXISTS {db}.raw_events
(
    event_uid          String,
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    vendor             LowCardinality(String),
    zone               LowCardinality(String),
    collector_id       LowCardinality(String),
    transport          LowCardinality(String),
    listener           LowCardinality(String),
    peer_ip            String,
    -- IF-ENVELOPE types these as plain ints with no range: a collector that
    -- reports a nonsense port must not stall the index (P2), so they are wide
    -- and Nullable where the contract allows null.
    peer_port          Nullable(UInt32),
    custody            LowCardinality(String),
    auth_method        LowCardinality(String),
    auth_key_id        LowCardinality(String),
    received_time      DateTime64(9, 'UTC'),
    seq_no             Nullable(Int64),
    raw_topic          LowCardinality(String),
    raw_partition      UInt16,
    raw_offset         UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    raw_sha256         FixedString(64),
    raw_len            UInt32,
    framing_method     LowCardinality(String),
    framing_truncated  Bool,
    framing_parts      UInt32,
    salt               Nullable(UInt32),
    -- First 256 decoded characters, for search result lists. Never the raw bytes.
    raw_preview        String CODEC(ZSTD(3)),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_event_uid event_uid TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(received_time)
ORDER BY (tenant_id, source_id, received_time)
TTL toDateTime(received_time) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

-- One row per (event, revision). Replay emits revision + 1 as a new row; a re-delivered
-- copy of the same (event_uid, revision) collapses on merge.
CREATE TABLE IF NOT EXISTS {db}.norm_lineage
(
    event_uid          String,
    revision           UInt16,
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    raw_topic          LowCardinality(String),
    raw_partition      UInt16,
    raw_offset         UInt64,
    raw_sha256         FixedString(64),
    contract_ref       LowCardinality(String),   -- '' when null
    template_sig       LowCardinality(String),
    template_id        LowCardinality(String),   -- '' when null
    tier               UInt8,
    conformance        LowCardinality(String),
    class_uid          UInt32,
    category           LowCardinality(String),
    norm_topic         LowCardinality(String),
    produced_at        DateTime64(9, 'UTC'),
    replay             Bool,
    replay_job_id      String,
    search_terms       Array(String),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_search_terms search_terms TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_template_sig template_sig TYPE bloom_filter(0.01) GRANULARITY 1
)
ENGINE = ReplacingMergeTree(produced_at)
PARTITION BY toYYYYMM(produced_at)
ORDER BY (event_uid, revision)
TTL toDateTime(produced_at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

-- The full IF-NORM-EVENT per revision, exactly as it was on norm.<category>.
CREATE TABLE IF NOT EXISTS {db}.norm_events
(
    event_uid          String,
    revision           UInt16,
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    class_uid          UInt32,
    tier               UInt8,
    received_time      DateTime64(9, 'UTC'),
    clock_skew_ms      Nullable(Int64),
    ocsf_json          String CODEC(ZSTD(3)),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree
PARTITION BY toYYYYMM(received_time)
ORDER BY (event_uid, revision)
TTL toDateTime(received_time) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

-- Filled directly by integrity (B3), not by the indexer. Signed roots are evidence, so
-- this table has no TTL.
CREATE TABLE IF NOT EXISTS {db}.window_roots
(
    window_id           String,
    window_start        DateTime('UTC'),
    window_end          DateTime('UTC'),
    leaf_count          UInt32,
    root                FixedString(64),
    prev_signed_sha256  FixedString(64),
    sig_b64             String,
    key_id              LowCardinality(String),
    immudb_tx           UInt64,
    immudb_verified     Bool,
    inserted_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY window_id;

-- IF-VAULT-INDEX kind=event. Emitted only at seal, so every row is sealed.
CREATE TABLE IF NOT EXISTS {db}.vault_locations
(
    event_uid          String,
    raw_topic          LowCardinality(String),
    raw_partition      UInt16,
    raw_offset         UInt64,
    segment_id         String,
    record_idx         UInt32,
    chain_hash         FixedString(64),
    sealed             Bool DEFAULT true,
    sealed_at          DateTime64(9, 'UTC'),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(sealed_at)
PARTITION BY toYYYYMM(sealed_at)
ORDER BY event_uid
TTL toDateTime(sealed_at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

-- IF-VAULT-INDEX kind=segment. `version` 0 = written by the indexer at seal; integrity
-- (B3) re-inserts the row with version >= 1 and window_id set, which wins on merge.
CREATE TABLE IF NOT EXISTS {db}.segments
(
    segment_id         String,
    topic              LowCardinality(String),
    partition          UInt16,
    first_offset       UInt64,
    last_offset        UInt64,
    record_count       UInt32,
    prev_chain_hash    FixedString(64),
    last_chain_hash    FixedString(64),
    digest             FixedString(64),
    sealed_at          DateTime64(9, 'UTC'),
    window_id          String DEFAULT '',
    version            UInt32 DEFAULT 0,
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY toYYYYMM(sealed_at)
ORDER BY segment_id
TTL toDateTime(sealed_at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE TABLE IF NOT EXISTS {db}.receipts
(
    event_uid          String,
    revision           UInt16,
    route_id           LowCardinality(String),
    status             LowCardinality(String),
    detail             String,
    at                 DateTime64(9, 'UTC'),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(at)
ORDER BY (event_uid, route_id, at)
TTL toDateTime(at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE TABLE IF NOT EXISTS {db}.dlq_events
(
    event_uid          String,
    tenant_id          LowCardinality(String),
    source_id          LowCardinality(String),
    tier               UInt8,
    reason_code        LowCardinality(String),
    reason_detail      String,
    contract_ref       LowCardinality(String),   -- '' when null
    template_sig       LowCardinality(String),
    text_masked        String CODEC(ZSTD(3)),
    parse_path         Array(LowCardinality(String)),
    produced_at        DateTime64(9, 'UTC'),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_event_uid event_uid TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(produced_at)
ORDER BY (source_id, template_sig, produced_at)
TTL toDateTime(produced_at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE TABLE IF NOT EXISTS {db}.shadow_diffs
(
    event_uid          String,
    contract_id        LowCardinality(String),
    active_ref         LowCardinality(String),   -- '' when null
    candidate_ref      LowCardinality(String),
    active_tier        UInt8,
    candidate_tier     UInt8,
    changed_fields     Array(LowCardinality(String)),
    regressions        Array(LowCardinality(String)),
    produced_at        DateTime64(9, 'UTC'),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_event_uid event_uid TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(produced_at)
ORDER BY (contract_id, produced_at)
TTL toDateTime(produced_at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;

CREATE TABLE IF NOT EXISTS {db}.audit_log
(
    actor              LowCardinality(String),
    role               LowCardinality(String),
    action             LowCardinality(String),
    target             String,
    detail             String,
    at                 DateTime64(9, 'UTC'),
    kafka_topic        LowCardinality(String),
    kafka_partition    UInt16,
    kafka_offset       UInt64,
    kafka_ts           DateTime64(3, 'UTC'),
    indexed_at         DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(at)
ORDER BY at
TTL toDateTime(at) + toIntervalDay({ttl_days})
SETTINGS non_replicated_deduplication_window = 1000;
