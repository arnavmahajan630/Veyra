"""Kafka -> ClickHouse lineage indexer (B1, v1 ADR-07).

One consumer group (``lineage-indexer``) reads every topic the index needs and
dispatches each record by topic to a per-table buffer. All buffers flush together at
``VEYRA_INDEX_BATCH_ROWS`` buffered rows or ``VEYRA_INDEX_BATCH_MS`` after the first
unflushed record, whichever comes first.

Delivery is at-least-once, and duplicates never reach ClickHouse:

1. Offsets are committed **only after** every table in the batch acknowledged its INSERT.
   A crash before the commit means the batch is read again after the restart.
2. An INSERT retried inside one run carries the same ``insert_deduplication_token``, so
   ClickHouse ignores the second copy (MVs included).
3. After a restart, the batch that was inserted but never committed is recognised by
   the Kafka position stored on every row. When a partition is assigned, the indexer
   loads the ``(offset, kafka timestamp)`` pairs already in ClickHouse from the
   committed offset on, and skips exactly those records. The timestamp check means a
   recreated topic (offsets start again at 0) is never mistaken for old data.

Records that fail contract validation are logged, counted and skipped (their offsets
are still committed), so one bad record cannot stall the index.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from typing import Any, cast

from confluent_kafka import Consumer, KafkaError, Message, TopicPartition
from prometheus_client import Counter, Histogram

from lineage_indexer.settings import IndexerSettings
from lineage_indexer.sink import ClickHouseSink, Ranges, dedup_token
from veyra_common.kafka import make_consumer
from veyra_common.topics import (
    NORM_PATTERN,
    RAW_PATTERN,
    TOPIC_AUDIT,
    TOPIC_DLQ,
    TOPIC_LINEAGE,
    TOPIC_RECEIPTS,
    TOPIC_SHADOW,
    TOPIC_VAULT_INDEX,
)
from veyra_lineage.rows import KafkaPos, Row, TableSpec, build_row, tables_for

log = logging.getLogger(__name__)

SUBSCRIPTION: tuple[str, ...] = (
    RAW_PATTERN,
    NORM_PATTERN,
    TOPIC_LINEAGE,
    TOPIC_VAULT_INDEX,
    TOPIC_RECEIPTS,
    TOPIC_DLQ,
    TOPIC_SHADOW,
    TOPIC_AUDIT,
)

ROWS = Counter("veyra_index_rows_total", "Rows inserted into ClickHouse", ["table"])
SKIPPED = Counter("veyra_index_skipped_total", "Records not inserted", ["reason"])
FLUSH_SECONDS = Histogram(
    "veyra_index_flush_seconds",
    "Insert-all-tables + commit time per batch",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

TopicPart = tuple[str, int]


class Indexer:
    def __init__(self, cfg: IndexerSettings, consumer: Consumer, sink: ClickHouseSink) -> None:
        self.cfg = cfg
        self.consumer = consumer
        self.sink = sink
        self._stop = False
        self._buffers: dict[str, list[Row]] = defaultdict(list)
        self._specs: dict[str, TableSpec] = {}
        self._ranges: dict[str, Ranges] = defaultdict(dict)
        self._buffered = 0
        # Highest offset consumed (inserted, skipped or rejected) per partition since
        # the last commit. The commit is these + 1.
        self._pending: dict[TopicPart, int] = {}
        self._first_pending_at: float | None = None
        # Replay guard: {partition: {offset: kafka_ts_ms}} of rows already indexed.
        self._guard: dict[TopicPart, dict[int, int]] = {}
        self._guard_max: dict[TopicPart, int] = {}
        # Partitions assigned but not yet checked against ClickHouse.
        self._to_arm: set[TopicPart] = set()
        self.inserts = 0
        self.commits = 0
        self.records = 0

    # ---------------------------------------------------------------- construction
    @classmethod
    def create(cls, cfg: IndexerSettings, sink: ClickHouseSink) -> Indexer:
        consumer = make_consumer(
            cfg.index_group,
            cfg=cfg,
            # New raw.<vendor> topics appear when a source is onboarded; the default
            # 5-minute metadata refresh would leave them unindexed for that long.
            **{"topic.metadata.refresh.interval.ms": cfg.index_metadata_refresh_ms},
        )
        indexer = cls(cfg, consumer, sink)
        consumer.subscribe(
            list(SUBSCRIPTION),
            on_assign=indexer._on_assign,
            on_revoke=indexer._on_revoke,
            on_lost=indexer._on_lost,
        )
        return indexer

    # ---------------------------------------------------------------- lifecycle
    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        log.info("indexer starting", extra={"group": self.cfg.index_group})
        try:
            while not self._stop:
                self.poll_once()
            self.flush()
        finally:
            self.consumer.close()
            log.info(
                "indexer stopped",
                extra={"records": self.records, "inserts": self.inserts, "commits": self.commits},
            )

    def poll_once(self) -> None:
        timeout = 0.1
        if self._first_pending_at is not None:
            left = self.cfg.index_batch_ms / 1000 - (time.monotonic() - self._first_pending_at)
            timeout = min(timeout, max(left, 0.0))
        room = max(self.cfg.index_batch_rows - self._buffered, 1)
        for msg in self.consumer.consume(num_messages=room, timeout=timeout):
            self.handle(msg)
        if self._due():
            self.flush()

    def _due(self) -> bool:
        if self._first_pending_at is None:
            return False
        if self._buffered >= self.cfg.index_batch_rows:
            return True
        return (time.monotonic() - self._first_pending_at) * 1000 >= self.cfg.index_batch_ms

    # ---------------------------------------------------------------- records
    def handle(self, msg: Message) -> None:
        err = msg.error()
        if err is not None:
            if err.code() != KafkaError._PARTITION_EOF:
                log.error("consume error", extra={"error": str(err)})
            return
        # A delivered (non-error) message always carries these; the None in
        # confluent_kafka's signatures is for error messages, handled above.
        topic = cast(str, msg.topic())
        partition = cast(int, msg.partition())
        offset = cast(int, msg.offset())
        key = (topic, partition)
        self._pending[key] = offset
        if self._first_pending_at is None:
            self._first_pending_at = time.monotonic()
        self.records += 1
        _, ts_ms = msg.timestamp()

        if key in self._to_arm:
            self._arm_guard(key, offset)
        if self._already_indexed(key, offset, ts_ms):
            SKIPPED.labels("already_indexed").inc()
            return
        value = msg.value()
        if value is None:
            SKIPPED.labels("tombstone").inc()
            return
        try:
            spec, row = build_row(topic, value, KafkaPos(topic, partition, offset, ts_ms))
        except Exception as exc:
            SKIPPED.labels("invalid").inc()
            log.error(
                "record rejected",
                extra={"topic": topic, "partition": partition, "offset": offset, "error": str(exc)},
            )
            return
        self._specs[spec.name] = spec
        self._buffers[spec.name].append(row)
        ranges = self._ranges[spec.name]
        first, _ = ranges.get(key, (offset, offset))
        ranges[key] = (first, offset)
        self._buffered += 1

    def _arm_guard(self, key: TopicPart, offset: int) -> None:
        """Load the rows this partition already has in ClickHouse from ``offset`` on.

        The first record delivered after an assignment sits exactly at the resume point,
        so anything at or after it that is already indexed came from a batch a previous
        run inserted but never committed.
        """
        self._to_arm.discard(key)
        topic, partition = key
        try:
            seen = self.sink.indexed_positions(
                tables_for(topic), topic, partition, offset, self.cfg.index_guard_rows
            )
        except Exception:
            # Failing to arm risks duplicates, never loss; ReplacingMergeTree still
            # collapses them for the tables that matter.
            log.exception("could not arm the replay guard", extra={"topic": topic})
            return
        if seen:
            self._guard[key] = seen
            self._guard_max[key] = max(seen)
            log.info(
                "replay guard armed",
                extra={"topic": topic, "partition": partition, "rows": len(seen)},
            )

    def _already_indexed(self, key: TopicPart, offset: int, ts_ms: int) -> bool:
        guard = self._guard.get(key)
        if guard is None:
            return False
        if offset > self._guard_max[key]:
            del self._guard[key], self._guard_max[key]  # past the uncommitted tail
            return False
        return guard.get(offset) == ts_ms

    # ---------------------------------------------------------------- flush
    def flush(self, *, skip_commit: frozenset[TopicPart] = frozenset()) -> None:
        """Insert every buffered table, then commit. Nothing is committed on failure."""
        if not self._pending:
            return
        started = time.monotonic()
        for name, rows in self._buffers.items():
            if not rows:
                continue
            spec = self._specs[name]
            self.sink.insert(spec, rows, dedup_token(name, self._ranges[name]))
            ROWS.labels(name).inc(len(rows))
            self.inserts += 1
            self._maybe_crash()
        offsets = [
            TopicPartition(t, p, off + 1)
            for (t, p), off in self._pending.items()
            if (t, p) not in skip_commit
        ]
        if offsets:
            self.consumer.commit(offsets=offsets, asynchronous=False)
            self.commits += 1
        self._buffers.clear()
        self._ranges.clear()
        self._pending.clear()
        self._buffered = 0
        self._first_pending_at = None
        FLUSH_SECONDS.observe(time.monotonic() - started)

    def _maybe_crash(self) -> None:
        """Test hook (VEYRA_INDEX_CRASH_AFTER_INSERTS): die between insert and commit."""
        n = self.cfg.index_crash_after_inserts
        if n and self.inserts >= n:
            log.error("crash hook: exiting after insert, before commit", extra={"inserts": n})
            logging.shutdown()
            os._exit(3)

    # ---------------------------------------------------------------- rebalance
    # librdkafka runs these on the poll thread and expects them to return promptly: a
    # blocking Kafka or ClickHouse call here can outlast the rebalance and, during a
    # close, fail outright. So they only record what to do; the work happens in the loop.
    def _on_assign(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        for tp in partitions:
            if tables_for(tp.topic):
                self._to_arm.add((tp.topic, tp.partition))

    def _on_revoke(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        # Commit what we have while we still own the partitions.
        try:
            self.flush()
        except Exception:
            log.exception("flush on revoke failed; the batch will be re-read")
        self._forget(partitions)

    def _on_lost(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        # The partitions already belong to someone else: insert (the guard makes that
        # harmless) but do not commit for them.
        lost = frozenset((tp.topic, tp.partition) for tp in partitions)
        try:
            self.flush(skip_commit=lost)
        except Exception:
            log.exception("flush on lost partitions failed")
        self._forget(partitions)

    def _forget(self, partitions: list[TopicPartition]) -> None:
        for tp in partitions:
            key = (tp.topic, tp.partition)
            self._to_arm.discard(key)
            self._guard.pop(key, None)
            self._guard_max.pop(key, None)

    def stats(self) -> dict[str, Any]:
        return {"records": self.records, "inserts": self.inserts, "commits": self.commits}
