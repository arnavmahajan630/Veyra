"""Kafka helpers with the durability settings IF-TOPICS mandates everywhere.

Producers: ``acks=all``, ``enable.idempotence=true``, ``compression.type=zstd``.
Consumers: ``isolation.level=read_committed``, manual commits only.

:class:`TxnProcessor` is the exactly-once loop every stateful service uses
(normalizer, archiver): consume a batch, transform it, produce the outputs, send the
consumed offsets **inside the transaction**, commit. A crash mid-batch aborts, so the
outputs and the offsets move together — no duplicates, no gaps.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer, TopicPartition

from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)


@dataclass(slots=True)
class OutputRecord:
    """One message a processing function wants produced inside the transaction."""

    topic: str
    value: bytes
    key: str | bytes | None = None
    headers: list[tuple[str, bytes]] = field(default_factory=list)


ProcessFn = Callable[[list[Message]], Iterable[OutputRecord]]


def _base_conf(cfg: Settings) -> dict[str, Any]:
    return {
        "bootstrap.servers": cfg.kafka_bootstrap,
        "client.id": cfg.profile,
    }


def make_producer(
    transactional_id: str | None = None,
    cfg: Settings | None = None,
    **overrides: Any,
) -> Producer:
    """A durable producer. Pass ``transactional_id`` to get a transactional one.

    Transactions are initialised by the caller (``TxnProcessor`` does it), because
    ``init_transactions()`` blocks until the coordinator answers.
    """
    s = cfg or settings
    conf: dict[str, Any] = {
        **_base_conf(s),
        "acks": "all",
        "enable.idempotence": True,
        "compression.type": "zstd",
        "linger.ms": 5,
        "max.in.flight.requests.per.connection": 5,
        "delivery.timeout.ms": 120_000,
    }
    if transactional_id:
        conf["transactional.id"] = transactional_id
    conf.update(overrides)
    return Producer(conf)


def make_consumer(
    group: str,
    topics: Sequence[str] | None = None,
    *,
    pattern: str | None = None,
    cfg: Settings | None = None,
    auto_offset_reset: str = "earliest",
    **overrides: Any,
) -> Consumer:
    """A read-committed consumer with manual commits, subscribed to ``topics``/``pattern``."""
    s = cfg or settings
    conf: dict[str, Any] = {
        **_base_conf(s),
        "group.id": group,
        "enable.auto.commit": False,
        "isolation.level": "read_committed",
        "auto.offset.reset": auto_offset_reset,
        "session.timeout.ms": 45_000,
        "max.poll.interval.ms": 300_000,
        "partition.assignment.strategy": "cooperative-sticky",
    }
    conf.update(overrides)
    consumer = Consumer(conf)
    subscription = list(topics or [])
    if pattern:
        subscription.append(pattern)
    if subscription:
        consumer.subscribe(subscription)
    return consumer


def read_compacted(
    topic: str,
    *,
    group: str,
    cfg: Settings | None = None,
    idle_ms: int = 1500,
) -> dict[str, bytes | None]:
    """Read a compacted topic from the beginning into ``{key: value}``.

    A ``None`` value is a tombstone. Returns once the topic has been quiet for
    ``idle_ms`` — that is the "first full read of control completed" readiness gate
    the normalizer and gateway wait on.
    """
    s = cfg or settings
    consumer = make_consumer(group, [topic], cfg=s, auto_offset_reset="earliest")
    state: dict[str, bytes | None] = {}
    last_data = time.monotonic()
    try:
        while (time.monotonic() - last_data) * 1000 < idle_ms:
            msg = consumer.poll(0.2)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(msg.error())
            key = msg.key().decode("utf-8") if msg.key() else ""
            state[key] = msg.value()
            last_data = time.monotonic()
    finally:
        consumer.close()
    return state


class TxnProcessor:
    """Exactly-once consume-transform-produce loop.

    ``fn`` receives a batch of messages and returns the records to produce. Raising
    from ``fn`` aborts the transaction and retries the batch; after
    ``poison_max_retries`` attempts the batch is skipped and ``on_poison`` is called,
    so one unparseable record can never stall the pipeline (A4 crash-loop guard).
    """

    def __init__(
        self,
        *,
        name: str,
        group: str,
        topics: Sequence[str] | None = None,
        pattern: str | None = None,
        fn: ProcessFn,
        instance: str = "0",
        cfg: Settings | None = None,
        on_poison: Callable[[list[Message], Exception], Iterable[OutputRecord]] | None = None,
    ) -> None:
        self.cfg = cfg or settings
        self.name = name
        self.fn = fn
        self.on_poison = on_poison
        self.consumer = make_consumer(group, topics, pattern=pattern, cfg=self.cfg)
        self.producer = make_producer(f"{name}-{instance}", cfg=self.cfg)
        self.producer.init_transactions()
        self._stop = False
        self._attempts = 0
        self.batches = 0
        self.records = 0

    # ---------------------------------------------------------------- lifecycle
    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT stop the loop after the current transaction commits."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: self.stop())

    def stop(self) -> None:
        self._stop = True

    def close(self) -> None:
        try:
            self.producer.flush(10)
        finally:
            self.consumer.close()

    # ---------------------------------------------------------------- loop
    def poll_batch(self) -> list[Message]:
        """Collect up to ``norm_batch_max`` messages, or whatever arrives in ``norm_batch_ms``."""
        deadline = time.monotonic() + self.cfg.norm_batch_ms / 1000
        batch: list[Message] = []
        while len(batch) < self.cfg.norm_batch_max and time.monotonic() < deadline:
            msg = self.consumer.poll(0.05)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                log.error("consume error", extra={"error": str(msg.error())})
                continue
            batch.append(msg)
        return batch

    def _positions(self, batch: list[Message]) -> list[TopicPartition]:
        highest: dict[tuple[str, int], int] = {}
        for msg in batch:
            key = (msg.topic(), msg.partition())
            highest[key] = max(highest.get(key, -1), msg.offset())
        return [TopicPartition(t, p, off + 1) for (t, p), off in highest.items()]

    def process_batch(self, batch: list[Message]) -> None:
        """One transaction: outputs and consumed offsets commit together."""
        self.producer.begin_transaction()
        try:
            for out in self.fn(batch):
                self.producer.produce(
                    out.topic, value=out.value, key=out.key, headers=out.headers or None
                )
            self.producer.send_offsets_to_transaction(
                self._positions(batch), self.consumer.consumer_group_metadata()
            )
            self.producer.commit_transaction()
        except Exception:
            self.producer.abort_transaction()
            raise
        self.batches += 1
        self.records += len(batch)
        self._attempts = 0

    def run(self) -> None:
        """Run until :meth:`stop` is called."""
        log.info("txn loop starting", extra={"service": self.name})
        while not self._stop:
            batch = self.poll_batch()
            if not batch:
                continue
            try:
                self.process_batch(batch)
            except Exception as exc:
                self._attempts += 1
                log.error(
                    "batch failed",
                    extra={"attempt": self._attempts, "size": len(batch), "error": str(exc)},
                )
                if self._attempts >= self.cfg.poison_max_retries:
                    self._quarantine(batch, exc)
        self.close()

    def _quarantine(self, batch: list[Message], exc: Exception) -> None:
        """Skip a poisonous batch, after emitting whatever ``on_poison`` produces."""
        self.producer.begin_transaction()
        try:
            if self.on_poison:
                for out in self.on_poison(batch, exc):
                    self.producer.produce(out.topic, value=out.value, key=out.key)
            self.producer.send_offsets_to_transaction(
                self._positions(batch), self.consumer.consumer_group_metadata()
            )
            self.producer.commit_transaction()
            log.error("batch quarantined", extra={"size": len(batch), "error": str(exc)})
        except Exception:
            self.producer.abort_transaction()
            raise
        finally:
            self._attempts = 0
