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
import os
import signal
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer, TopicPartition

from veyra_common.inflight import BatchId, InflightJournal
from veyra_common.settings import Settings, settings


def _coords(msg: Message) -> tuple[str, int, int]:
    """``(topic, partition, offset)`` of a polled message.

    confluent types all three as optional, because they are unset on a message the client
    *builds*. A message that came back from ``poll`` always has them, and a batch whose
    coordinates were unknown could not be committed at all — so this asserts rather than
    inventing a default offset.
    """
    topic, partition, offset = msg.topic(), msg.partition(), msg.offset()
    if topic is None or partition is None or offset is None:
        raise KafkaException(f"consumed message without coordinates: {msg!r}")
    return topic, partition, offset


log = logging.getLogger(__name__)


@dataclass(slots=True)
class OutputRecord:
    """One message a processing function wants produced inside the transaction."""

    topic: str
    value: bytes
    key: str | bytes | None = None
    headers: list[tuple[str, bytes]] = field(default_factory=list)


ProcessFn = Callable[[list[Message]], Iterable[OutputRecord]]


class PoisonBatch(Exception):
    """Raised for the benefit of ``on_poison`` when a batch is skipped without being processed."""


class TxnStalled(RuntimeError):
    """Kafka would neither finish the transaction nor abort it; only a new process can carry on.

    Replacing the process is safe by construction: ``init_transactions`` in the new one fences
    this one and resolves whatever it left open, and consumption resumes from the committed
    offsets.
    """


def exit_for_restart(reason: str) -> None:
    """Leave at once, so the restart policy starts a clean process.

    Not ``sys.exit``: the interpreter's shutdown waits for librdkafka to close, and librdkafka
    being stuck is the reason for leaving.
    """
    log.critical("kafka transactions are stuck; exiting for a restart", extra={"reason": reason})
    logging.shutdown()
    os._exit(1)


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
        "delivery.timeout.ms": s.kafka_delivery_timeout_ms,
    }
    if transactional_id:
        conf["transactional.id"] = transactional_id
        # Must be >= delivery.timeout.ms, or librdkafka rejects the producer outright
        # (_INVALID_ARG). The broker caps it at transaction.max.timeout.ms (15 min).
        conf["transaction.timeout.ms"] = max(s.kafka_txn_timeout_ms, s.kafka_delivery_timeout_ms)
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
            err = msg.error()
            if err is not None:
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                raise KafkaException(err)
            raw_key = msg.key()
            key = raw_key.decode("utf-8") if raw_key else ""
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

    The attempt count is also journalled to ``data/state/<name>_inflight`` before each batch, so a
    record that kills the *process* rather than raising is quarantined on the next restart instead
    of crash-looping forever. See :mod:`veyra_common.inflight`.

    A transaction that Kafka fails is not the records' fault and is never counted against them.
    It is aborted and the batch read again. When it cannot even be aborted, ``on_stall`` is called,
    which by default replaces the process (:func:`exit_for_restart`).

    A watchdog does the same for the two states the loop was seen unable to leave by itself, both
    after a broker lost and regained its partitions (librdkafka 2.15), and both with the process
    still looking healthy:

    * a transaction still open after ``kafka_txn_timeout_ms``, when the broker has already
      rolled it back. The calls are given that limit too, but ``send_offsets_to_transaction``
      waited for good on a reply that never came, and a loop blocked there never polls;
    * a consumer put out of its group that polls on and never asks to rejoin.
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
        journal: InflightJournal | None = None,
        on_stall: Callable[[str], None] = exit_for_restart,
    ) -> None:
        self.cfg = cfg or settings
        self.name = name
        self.fn = fn
        self.on_poison = on_poison
        self.on_stall = on_stall
        # One journal per instance: instances share data/state, and one file between them means
        # each overwrites and clears the others' record. Instance 0 keeps the documented name.
        owner = name if instance == "0" else f"{name}-{instance}"
        self.journal = journal or InflightJournal.for_service(owner, cfg=self.cfg)
        # Round-robin deals the sorted partition list out one at a time, so every topic is spread
        # over every instance. `cooperative-sticky` only evens the count: with six topics and two
        # of them busy, one instance was seen holding all of one busy topic and five holding none
        # of it. Its full revoke on a rebalance costs nothing here: the open transaction is
        # aborted and the batch read again by whoever owns it next.
        self.consumer = make_consumer(
            group,
            topics,
            pattern=pattern,
            cfg=self.cfg,
            **{"partition.assignment.strategy": "roundrobin"},
        )
        self.producer = make_producer(f"{name}-{instance}", cfg=self.cfg)
        self.producer.init_transactions()
        # The broker rolls a transaction back at this age, so a call still waiting cannot succeed.
        self._txn_wait_s = self.cfg.kafka_txn_timeout_ms / 1000
        self._txn_began: float | None = None
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
            err = msg.error()
            if err is not None:
                if err.code() == KafkaError._PARTITION_EOF:
                    continue
                log.error("consume error", extra={"error": str(err)})
                continue
            batch.append(msg)
        return batch

    @staticmethod
    def batch_id(batch: list[Message]) -> BatchId:
        """Stable identity of a batch: the offset range held per partition."""
        bounds: dict[tuple[str, int], tuple[int, int]] = {}
        for msg in batch:
            topic, partition, offset = _coords(msg)
            key = (topic, partition)
            low, high = bounds.get(key, (offset, offset))
            bounds[key] = (min(low, offset), max(high, offset))
        return tuple(
            (topic, partition, low, high)
            for (topic, partition), (low, high) in sorted(bounds.items())
        )

    def _positions(self, batch: list[Message]) -> list[TopicPartition]:
        highest: dict[tuple[str, int], int] = {}
        for msg in batch:
            topic, partition, offset = _coords(msg)
            key = (topic, partition)
            highest[key] = max(highest.get(key, -1), offset)
        return [TopicPartition(t, p, off + 1) for (t, p), off in highest.items()]

    def _transact(self, batch: list[Message], outputs: Iterable[OutputRecord]) -> None:
        """Produce ``outputs`` and move past ``batch`` in one transaction, every call bounded."""
        self._txn_began = time.monotonic()
        self.producer.begin_transaction()
        for out in outputs:
            # confluent types `headers` as a mapping or a list of (str, str|bytes|None);
            # ours is always a list of (str, bytes), which is that type.
            headers: Any = out.headers or None
            self.producer.produce(out.topic, value=out.value, key=out.key, headers=headers)
        self.producer.send_offsets_to_transaction(
            self._positions(batch), self.consumer.consumer_group_metadata(), self._txn_wait_s
        )
        self.producer.commit_transaction(self._txn_wait_s)
        self._txn_began = None

    def process_batch(self, batch: list[Message]) -> None:
        """One transaction: outputs and consumed offsets commit together.

        ``fn`` runs before the transaction begins, so what it raises is about the records and
        leaves nothing to abort; a ``KafkaException`` from here on is about Kafka.
        """
        self._transact(batch, list(self.fn(batch)))
        self.batches += 1
        self.records += len(batch)
        self._attempts = 0
        self.journal.clear()

    def run(self) -> None:
        """Run until :meth:`stop` is called."""
        log.info("txn loop starting", extra={"service": self.name})
        threading.Thread(target=self._watch, name="txn-watchdog", daemon=True).start()
        try:
            while not self._stop:
                batch = self.poll_batch()
                if not batch:
                    continue
                try:
                    self._handle(batch)
                except KafkaException as exc:
                    self._recover(batch, exc)
        finally:
            self._stop = True  # the watchdog's cue to leave as well
        self.close()

    def _handle(self, batch: list[Message]) -> None:
        """One batch: what ``fn`` raises counts against the records, what Kafka raises does not."""
        identity = self.batch_id(batch)
        if self.journal.poisonous(identity):
            # This exact batch already took us down `poison_max_retries` times. Do not hand it
            # to `fn` again — processing it is the thing that kills us.
            self._quarantine(batch, PoisonBatch(f"crash-looped on {identity}"))
            return
        attempts = self.journal.begin(identity)
        try:
            self.process_batch(batch)
        except KafkaException:
            raise
        except Exception as exc:
            self._attempts = max(self._attempts + 1, attempts)
            log.error(
                "batch failed",
                extra={"attempt": self._attempts, "size": len(batch), "error": str(exc)},
            )
            if self._attempts >= self.cfg.poison_max_retries:
                self._quarantine(batch, exc)
            else:
                self._rewind(batch)

    def _watch(self) -> None:
        """Call ``on_stall`` for the two stuck states described on the class."""
        limit = self._txn_wait_s
        groupless: float | None = None
        while not self._stop:
            time.sleep(min(1.0, limit / 4))
            now = time.monotonic()
            began = self._txn_began
            # No member id is normal while joining, and for as long as Kafka is away.
            groupless = None if self.consumer.memberid() else groupless or now
            if began is not None and now - began > limit:
                reason = "a transaction outlived"
            elif groupless is not None and now - groupless > limit:
                reason = "outside its consumer group for"
            else:
                continue
            self.journal.clear()  # Kafka's doing, so not an attempt against the records
            self.on_stall(f"{self.name}: {reason} {limit:.0f} s")
            return

    def _give_up(self, reason: str, cause: Exception) -> None:
        self.on_stall(reason)
        raise TxnStalled(reason) from cause

    def _recover(self, batch: list[Message], exc: KafkaException) -> None:
        """Undo a transaction Kafka failed and read ``batch`` again, or give the process up."""
        error = exc.args[0] if exc.args else None
        abortable = isinstance(error, KafkaError) and error.txn_requires_abort()
        log.error(
            "transaction failed",
            extra={"size": len(batch), "error": str(exc), "abortable": abortable},
        )
        # Whatever went wrong was not in the records, so a restart on this batch must not count
        # towards quarantining them.
        self.journal.clear()
        if not abortable:
            self._give_up(f"{self.name}: transaction cannot be completed: {exc}", exc)
        try:
            self.producer.abort_transaction(self._txn_wait_s)
        except KafkaException as abort_exc:
            self._give_up(f"{self.name}: transaction cannot be aborted: {abort_exc}", exc)
        self._txn_began = None
        self._rewind(batch)

    def _rewind(self, batch: list[Message]) -> None:
        """Put the consumer back at the start of ``batch``, so the next poll reads it again.

        Without this an uncommitted batch is simply skipped: the consumer's position is already
        past it, and the next batch's offsets commit over the gap.
        """
        first: dict[tuple[str, int], int] = {}
        for msg in batch:
            topic, partition, offset = _coords(msg)
            key = (topic, partition)
            first[key] = min(first.get(key, offset), offset)
        for (topic, partition), offset in first.items():
            try:
                self.consumer.seek(TopicPartition(topic, partition, offset))
            except KafkaException as exc:
                # No longer ours: a rebalance moved it, and its new owner starts from the
                # committed offset, which is this one.
                log.info(
                    "partition not rewound",
                    extra={"partition": f"{topic}[{partition}]", "error": str(exc)},
                )

    def _quarantine(self, batch: list[Message], exc: Exception) -> None:
        """Skip a poisonous batch, after emitting whatever ``on_poison`` produces."""
        outputs = list(self.on_poison(batch, exc)) if self.on_poison else []
        self._transact(batch, outputs)
        log.error("batch quarantined", extra={"size": len(batch), "error": str(exc)})
        self._attempts = 0
        self.journal.clear()
