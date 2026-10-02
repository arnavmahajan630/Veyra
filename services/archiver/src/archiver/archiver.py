"""Kafka ``raw.*`` -> encrypted vault segments (B2 prototype, v1 §8.1).

One consumer group (``archiver``) reads every ``raw.<vendor>`` topic and keeps one open
:class:`~veyra_evidence.segment.SegmentWriter` per ``(topic, partition)``. A segment seals
when it reaches ``VEYRA_SEGMENT_MAX_BYTES``, ``VEYRA_SEGMENT_MAX_SECONDS`` or
``VEYRA_SEGMENT_MAX_RECORDS``, and on shutdown or partition revoke.

**Offsets are committed only after the segment file is sealed on disk**, so a crash can
lose a sealed segment's *commit* but never a record: the partition is re-read from the last
committed offset and the events land in a new segment. That direction (possible duplicate
work, never loss) is the one P2 asks for.

The chain resumes from the newest sealed segment's ``last_chain_hash`` for that partition,
so the hash chain survives restarts.

At each seal the archiver publishes IF-VAULT-INDEX to the ``vault_index`` topic: one
``segment`` record plus one ``event`` record per archived envelope. The lineage indexer turns
those into the ``segments`` and ``vault_locations`` tables, which is what lets the evidence
API find an event by index instead of scanning every segment in the vault, and what fills the
console's vault panel. Publishing happens *after* the file is durable and *before* the offset
commit, so the worst case is a re-published record (the index collapses duplicates on
``(event_uid, revision)`` / ``segment_id``), never a segment with no index entry.

Prototype scope: no Kafka transactions, no chattr, no crash injection, no chain-epoch anomaly
recovery. Those are the rest of B2 and B3.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from typing import Any, cast

from confluent_kafka import Consumer, KafkaError, Message, TopicPartition
from prometheus_client import Counter, Gauge, Histogram

from archiver.settings import ArchiverSettings
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models.records import VaultIndexEvent, VaultIndexSegment
from veyra_common.topics import RAW_PATTERN, TOPIC_VAULT_INDEX
from veyra_evidence.keys import KeyProvider, get_key_provider
from veyra_evidence.segment import SealedSegment, SegmentWriter, latest_header

log = logging.getLogger(__name__)

SEGMENTS_SEALED = Counter(
    "veyra_vault_segments_sealed_total", "Segments sealed", ["topic", "partition"]
)
BYTES_TOTAL = Counter("veyra_vault_bytes_total", "Plaintext bytes archived")
RECORDS_TOTAL = Counter("veyra_vault_records_total", "Envelopes archived")
SKIPPED = Counter("veyra_vault_skipped_total", "Records not archived", ["reason"])
INDEX_PUBLISHED = Counter(
    "veyra_vault_index_published_total", "IF-VAULT-INDEX records produced", ["kind"]
)
INDEX_FAILED = Counter("veyra_vault_index_failed_total", "IF-VAULT-INDEX publishes that failed")
OPEN_AGE = Gauge(
    "veyra_vault_open_segment_age_seconds", "Age of the open segment", ["topic", "partition"]
)
SEAL_SECONDS = Histogram(
    "veyra_vault_seal_seconds",
    "Compress + encrypt + write + fsync time per segment",
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

TopicPart = tuple[str, int]


class Archiver:
    def __init__(
        self,
        cfg: ArchiverSettings,
        consumer: Consumer,
        keys: KeyProvider | None = None,
        producer: Any | None = None,
    ) -> None:
        self.cfg = cfg
        self.consumer = consumer
        self.keys = keys or get_key_provider(cfg)
        # None means "do not publish the vault index", which is what the unit tests use.
        self.producer = producer
        self._writers: dict[TopicPart, SegmentWriter] = {}
        # Per open segment: what each appended envelope needs for its vault_index record.
        self._appended: dict[TopicPart, list[tuple[str, int, int, str]]] = {}
        # Chain head per partition, carried across segments.
        self._heads: dict[TopicPart, bytes] = {}
        self._stop = False
        self.records = 0
        self.segments = 0
        self.sealed: list[SealedSegment] = []

    @classmethod
    def create(
        cls,
        cfg: ArchiverSettings,
        keys: KeyProvider | None = None,
        subscription: Sequence[str] | None = None,
    ) -> Archiver:
        r"""Build an archiver subscribed to ``^raw\..*``.

        ``subscription`` narrows that, which integration tests use to archive only their
        own throwaway topic instead of everything on the broker.
        """
        consumer = make_consumer(
            cfg.archive_group,
            cfg=cfg,
            **{"topic.metadata.refresh.interval.ms": cfg.archive_metadata_refresh_ms},
        )
        archiver = cls(cfg, consumer, keys, producer=make_producer(cfg=cfg))
        consumer.subscribe(
            list(subscription or [RAW_PATTERN]),
            on_assign=archiver._on_assign,
            on_revoke=archiver._on_revoke,
        )
        return archiver

    # ---------------------------------------------------------------- lifecycle
    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        log.info("archiver starting", extra={"group": self.cfg.archive_group})
        try:
            while not self._stop:
                self.poll_once()
            self.seal_all()
        finally:
            self.consumer.close()
            log.info("archiver stopped", extra={"records": self.records, "segments": self.segments})

    def poll_once(self, timeout: float = 0.2) -> None:
        for msg in self.consumer.consume(num_messages=500, timeout=timeout):
            key = self.handle(msg)
            # Check after *every* record, not once per poll batch: a batch can carry
            # hundreds of events, and a segment must never overshoot its size limit.
            if key is not None:
                writer = self._writers.get(key)
                if writer is not None and writer.should_seal(
                    max_records=self.cfg.segment_max_records
                ):
                    self.seal(key)
        # Catches the age trigger on partitions that have gone quiet.
        self.seal_due()

    # ---------------------------------------------------------------- records
    def handle(self, msg: Message) -> TopicPart | None:
        """Append one record. Returns the partition it landed in, or ``None`` if skipped."""
        err = msg.error()
        if err is not None:
            if err.code() != KafkaError._PARTITION_EOF:
                log.error("consume error", extra={"error": str(err)})
            return None
        topic = cast(str, msg.topic())
        partition = cast(int, msg.partition())
        offset = cast(int, msg.offset())
        value = msg.value()
        if value is None:
            SKIPPED.labels("empty").inc()
            return None
        try:
            envelope = json.loads(value)
            raw_sha256 = envelope["raw_sha256"]
            event_uid = envelope["event_uid"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # Never stall the vault on one malformed record; it stays in Kafka's own
            # retention and the DLQ path (A3) is where unparseable content is handled.
            SKIPPED.labels("malformed").inc()
            log.error(
                "record is not an envelope",
                extra={"topic": topic, "partition": partition, "offset": offset, "error": str(exc)},
            )
            return None

        key = (topic, partition)
        writer = self._writers.get(key)
        if writer is None:
            writer = self._open(key, offset)
        record_idx, chain_hash_hex = writer.append(offset, value, raw_sha256, event_uid)
        self._appended.setdefault(key, []).append((event_uid, record_idx, offset, chain_hash_hex))
        self.records += 1
        RECORDS_TOTAL.inc()
        BYTES_TOTAL.inc(len(value))
        OPEN_AGE.labels(topic, str(partition)).set(writer.age_seconds)
        return key

    def _open(self, key: TopicPart, first_offset: int) -> SegmentWriter:
        topic, partition = key
        writer = SegmentWriter(
            topic,
            partition,
            first_offset,
            prev_chain_hash=self._heads.get(key),
            key_provider=self.keys,
            vault_dir=self.cfg.vault_dir,
            cfg=self.cfg,
        )
        self._writers[key] = writer
        log.info(
            "segment opened",
            extra={"topic": topic, "partition": partition, "first_offset": first_offset},
        )
        return writer

    # ---------------------------------------------------------------- sealing
    def seal_due(self) -> None:
        for key, writer in list(self._writers.items()):
            if writer.should_seal(max_records=self.cfg.segment_max_records):
                self.seal(key)

    def seal_all(self) -> None:
        for key in list(self._writers):
            self.seal(key)

    def seal(self, key: TopicPart, *, commit: bool = True) -> SealedSegment | None:
        """Seal one partition's open segment, then commit its offsets.

        The order is what makes a restart safe: the file exists and is durable before
        Kafka is told we are done with those offsets.
        """
        writer = self._writers.pop(key, None)
        if writer is None or writer.record_count == 0:
            return None
        topic, partition = key
        started = time.monotonic()
        sealed = writer.seal()
        SEAL_SECONDS.observe(time.monotonic() - started)
        SEGMENTS_SEALED.labels(topic, str(partition)).inc()
        OPEN_AGE.labels(topic, str(partition)).set(0)
        self._heads[key] = bytes.fromhex(sealed.header["last_chain_hash_hex"])
        self.segments += 1
        self.sealed.append(sealed)
        # Before the commit: a crash between the two re-reads the partition and re-publishes,
        # which the index collapses. A commit first could leave a segment with no index rows.
        self._publish_index(key, sealed)
        if commit:
            # Only now: the evidence is on disk.
            self.consumer.commit(
                offsets=[TopicPartition(topic, partition, sealed.last_offset + 1)],
                asynchronous=False,
            )
        return sealed

    def _publish_index(self, key: TopicPart, sealed: SealedSegment) -> None:
        """Publish IF-VAULT-INDEX for one sealed segment: one segment row, one row per event.

        Best effort by design: the evidence is already on disk, and the index is a lookup
        shortcut. A broker that will not take these must not stop the vault, so a failure is
        logged and counted rather than raised — the evidence API falls back to scanning.
        """
        appended = self._appended.pop(key, [])
        if self.producer is None:
            return
        topic, partition = key
        sealed_at = str(sealed.header["sealed_at"])
        records: list[VaultIndexSegment | VaultIndexEvent] = [
            VaultIndexSegment(
                segment_id=sealed.segment_id,
                raw_topic=topic,
                partition=partition,
                first_offset=sealed.first_offset,
                last_offset=sealed.last_offset,
                record_count=sealed.record_count,
                prev_chain_hash_hex=str(sealed.header["prev_chain_hash_hex"]),
                last_chain_hash_hex=str(sealed.header["last_chain_hash_hex"]),
                segment_digest_hex=sealed.digest_hex,
                sealed_at=sealed_at,
            )
        ]
        records += [
            VaultIndexEvent(
                event_uid=event_uid,
                raw_topic=topic,
                partition=partition,
                offset=offset,
                segment_id=sealed.segment_id,
                record_idx=record_idx,
                chain_hash_hex=chain_hash_hex,
                sealed_at=sealed_at,
            )
            for event_uid, record_idx, offset, chain_hash_hex in appended
        ]
        try:
            for record in records:
                self.producer.produce(
                    TOPIC_VAULT_INDEX,
                    value=record.model_dump_json().encode("utf-8"),
                    key=sealed.segment_id.encode("utf-8"),
                )
                INDEX_PUBLISHED.labels(record.kind).inc()
            self.producer.flush(self.cfg.archive_index_publish_timeout_s)
        except Exception:
            INDEX_FAILED.inc()
            log.exception(
                "could not publish the vault index",
                extra={"segment_id": sealed.segment_id, "records": len(records)},
            )

    # ---------------------------------------------------------------- rebalance
    def _on_assign(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        """Resume each partition's chain from its newest sealed segment."""
        for tp in partitions:
            key = (tp.topic, tp.partition)
            if key in self._heads:
                continue
            try:
                header = latest_header(tp.topic, tp.partition, self.cfg.vault_dir, self.keys)
            except Exception:
                log.exception("could not read the last segment header", extra={"topic": tp.topic})
                continue
            if header is not None:
                self._heads[key] = bytes.fromhex(header["last_chain_hash_hex"])
                log.info(
                    "chain resumed",
                    extra={
                        "topic": tp.topic,
                        "partition": tp.partition,
                        "from_segment": header["segment_id"],
                        "last_offset": header["last_offset"],
                    },
                )

    def _on_revoke(self, _consumer: Consumer, partitions: list[TopicPartition]) -> None:
        """Seal before letting go, so no open segment is lost to a rebalance."""
        for tp in partitions:
            try:
                self.seal((tp.topic, tp.partition))
            except Exception:
                log.exception("seal on revoke failed", extra={"topic": tp.topic})

    def stats(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "segments": self.segments,
            "open": len(self._writers),
        }
