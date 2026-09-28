"""The indexer's ClickHouse side: synchronous batch inserts and the replay-guard lookup.

``async_insert=0``: an INSERT returns only once the part is written, so "ClickHouse
acknowledged" really means durable, and the offsets can be committed after it.

``insert_deduplication_token`` is derived from the exact Kafka ranges in the batch, so an
INSERT retried after a timeout (the server may have written it) is a no-op, including in
the materialized views (``deduplicate_blocks_in_dependent_materialized_views``).
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable, Sequence

from clickhouse_connect.driver.client import Client
from prometheus_client import Counter

from veyra_lineage.rows import Row, TableSpec

log = logging.getLogger(__name__)

INSERT_RETRIES = Counter(
    "veyra_index_insert_retries_total", "ClickHouse insert attempts that failed", ["table"]
)

INSERT_SETTINGS = {
    "async_insert": 0,
    "deduplicate_blocks_in_dependent_materialized_views": 1,
}

# (topic, partition) -> (first_offset, last_offset) of the rows in one batch
Ranges = dict[tuple[str, int], tuple[int, int]]


def dedup_token(table: str, ranges: Ranges) -> str:
    """Deterministic for a given set of Kafka ranges, so a retry reuses it."""
    parts = [f"{t}:{p}:{a}-{b}" for (t, p), (a, b) in sorted(ranges.items())]
    return hashlib.sha256(f"{table}|{','.join(parts)}".encode()).hexdigest()


class ClickHouseSink:
    def __init__(
        self, client: Client, db: str, *, retries: int = 5, backoff_s: float = 0.5
    ) -> None:
        self.client = client
        self.db = db
        self.retries = retries
        self.backoff_s = backoff_s

    def insert(self, spec: TableSpec, rows: Sequence[Row], token: str) -> None:
        """Insert one batch; retry with the same token; raise after ``retries`` failures."""
        attempt = 0
        while True:
            try:
                self.client.insert(
                    f"{self.db}.{spec.name}",
                    rows,
                    column_names=list(spec.columns),
                    settings={**INSERT_SETTINGS, "insert_deduplication_token": token},
                )
                return
            except Exception as exc:
                attempt += 1
                INSERT_RETRIES.labels(spec.name).inc()
                if attempt >= self.retries:
                    raise
                delay = self.backoff_s * 2 ** (attempt - 1)
                log.warning(
                    "insert failed, retrying",
                    extra={"table": spec.name, "attempt": attempt, "error": str(exc)},
                )
                time.sleep(delay)

    def indexed_positions(
        self,
        specs: Iterable[TableSpec],
        topic: str,
        partition: int,
        from_offset: int,
        limit: int,
    ) -> dict[int, int]:
        """``{offset: kafka_ts_ms}`` of rows already in ClickHouse for this partition,
        from ``from_offset`` on (at most ``limit`` per table).

        These are rows inserted by a previous run whose offsets were not committed.
        """
        found: dict[int, int] = {}
        for spec in specs:
            t_col, p_col, o_col, ts_col = spec.pos_columns
            rows = self.client.query(
                f"SELECT {o_col}, toUnixTimestamp64Milli({ts_col}) FROM {self.db}.{spec.name} "
                f"WHERE {t_col} = {{t:String}} AND {p_col} = {{p:UInt16}} "
                f"AND {o_col} >= {{o:UInt64}} ORDER BY {o_col} LIMIT {{n:UInt32}}",
                parameters={"t": topic, "p": partition, "o": max(from_offset, 0), "n": limit},
            ).result_rows
            for offset, ts_ms in rows:
                found[int(offset)] = int(ts_ms)
        return found
