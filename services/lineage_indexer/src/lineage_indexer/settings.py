"""The lineage indexer's own knobs, on top of the shared ones (IF-ENV).

Shared: ``VEYRA_INDEX_BATCH_ROWS``, ``VEYRA_INDEX_BATCH_MS``, ``VEYRA_LINEAGE_TTL_DAYS``,
``VEYRA_CH_URL`` / ``VEYRA_CLICKHOUSE_URL``, ``VEYRA_CLICKHOUSE_DB``.
"""

from __future__ import annotations

from veyra_common.settings import ServiceSettings


class IndexerSettings(ServiceSettings):
    service_name: str = "lineage_indexer"
    metrics_port: int = 8205  # IF-PORTS

    index_group: str = "lineage-indexer"
    index_insert_retries: int = 5
    # Upper bound on rows loaded per partition for the post-restart replay guard. One
    # uncommitted batch is at most VEYRA_INDEX_BATCH_ROWS rows, so 5x is ample.
    index_guard_rows: int = 10_000
    index_metadata_refresh_ms: int = 10_000
    # Seconds to keep retrying the first ClickHouse connection at startup.
    index_ch_wait_s: int = 120
    # Test hook only: exit right after the Nth insert, before its commit (AC2).
    index_crash_after_inserts: int = 0
