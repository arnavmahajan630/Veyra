# B1 — Lineage indexer, ClickHouse schema, query library

```
track: B   owner: B   status: todo
contracts: v1.5
depends_on: [S0]     unblocks: [CP1, B4, C5]
consumes: [IF-ENVELOPE, IF-NORM-EVENT, IF-LINEAGE, IF-VAULT-INDEX, IF-RECEIPT, IF-DLQ, IF-SHADOW, IF-AUDIT]
provides: [IF-CH-SCHEMA, packages/veyra_lineage.queries]
directories: [packages/veyra_lineage/, services/lineage_indexer/]
```

## Goal

A thin, fast index of every event (v1 ADR-07): where its raw bytes are, how it was normalized (every revision), where it was delivered, and the per-source/per-minute aggregates the console needs. All API SQL lives in one query library.

## Design

**Migrations.** `packages/veyra_lineage/migrations/NNN_*.sql`, applied idempotently at indexer startup (a `schema_migrations` table). Tables and materialized views per IF-CH-SCHEMA. Use `LowCardinality` for tenant, source, route and conformance. TTL on all event tables = `VEYRA_LINEAGE_TTL_DAYS`.

**Indexer.**
- One process, one consumer group `lineage-indexer`.
- Subscribes to `^raw\..*`, `norm.*`, `lineage`, `vault_index`, `receipts`, `dlq`, `shadow`, `audit`.
- Dispatches by topic to per-table batchers: flush at `VEYRA_INDEX_BATCH_ROWS` or `VEYRA_INDEX_BATCH_MS`.
- Insert via `clickhouse-connect` with `async_insert=0` and explicit batches.
- **Commit Kafka offsets only after ClickHouse acknowledges the insert** (at-least-once). Duplicates are handled by `ReplacingMergeTree` or dedup keys where they matter; for plain MergeTree tables, keep a `(event_uid, …)` dedup via `insert_deduplication_token` per batch.
- `raw_events` stores **no raw bytes**, only metadata plus `raw_preview` (the first 256 decoded chars, for search result lists).

**Query library** (`veyra_lineage/queries.py`): one function per API need, returning Pydantic models:
- `overview(tenant)`: EPS over the last 60 s, tier totals over the last 15 min, per-source strip, route status, vault status.
- `source_health(tenant)`: expected vs actual EPS (expected from control `source:*`, passed in), `last_seen`, tier mix, contract ref, clock skew p50.
- `search(q, tenant, limit)`: `event_uid` exact / sha prefix / `has(search_terms, q)` / `template_sig`.
- `event_detail(event_uid)`: lineage rows by revision, `norm_events` JSON by revision, vault location, receipts, raw_ref.
- `template_events(sig, source, limit)`: used for replay and backtest.
- `dlq_samples(source, sig, limit)`.
- `shadow_summary(contract_id, since)`.
- `route_stats(since)`.

Each function has a `p95 < 50 ms` target on the laptop with 1M rows. Benchmark it (`tools/bench/ch_queries.py`).

## Tasks
- [ ] 1. Migrations + the MVs (`mv_source_minute`, `mv_route_minute`).
- [ ] 2. Indexer with batching, commit-after-insert and dedup tokens.
- [ ] 3. Query library + Pydantic result models + fixtures (C5 uses these fixtures to build UI before the data is real).
- [ ] 4. A data generator for 1M synthetic rows (`tools/seed_ch.py`) + query benchmarks.
- [ ] 5. Integration test: `fake_raw` + `fake_norm` → counts reconcile across tables.

## Acceptance criteria
- [ ] AC1: At CP1 traffic, `raw_events` count == envelopes sent; `norm_lineage` count == norm events (per revision).
- [ ] AC2: Kill the indexer mid-stream → counts still reconcile after restart (no loss; duplicates collapsed).
- [ ] AC3: All query functions `p95 < 50 ms` on 1M rows (report the numbers).
- [ ] AC4: `search("103.21.4.77")` returns T3 events via `search_terms`.

## Settings
`VEYRA_INDEX_BATCH_ROWS` (2000), `VEYRA_INDEX_BATCH_MS` (500), `VEYRA_LINEAGE_TTL_DAYS`, `VEYRA_CH_URL`.

## Implementation notes
_(filled after execution)_
