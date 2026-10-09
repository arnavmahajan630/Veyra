"""The transaction loop when Kafka, rather than a record, is what fails.

Found by the pipeline load test: a short broker stall left every normalizer blocked forever inside
``send_offsets_to_transaction``, which waits without limit unless it is given one. A loop blocked
there never polls, so it never rejoins its group, and the process stays up looking healthy.

These tests drive the real loop over a scripted consumer and producer.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from confluent_kafka import KafkaError, KafkaException, TopicPartition

from veyra_common import kafka
from veyra_common.kafka import OutputRecord, TxnProcessor, TxnStalled
from veyra_common.settings import Settings

TOPIC = "raw.x"


class FakeMessage:
    def __init__(self, offset: int) -> None:
        self._offset = offset

    def topic(self) -> str:
        return TOPIC

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return self._offset

    def value(self) -> bytes:
        return str(self._offset).encode()

    def error(self) -> None:
        return None


class FakeConsumer:
    """One partition of messages; ``seek`` moves the read position like the real one."""

    def __init__(self, first: int, count: int) -> None:
        self.first = first
        self.log = [FakeMessage(first + i) for i in range(count)]
        self.position = 0
        self.seeks: list[int] = []
        self.on_idle: Callable[[], None] = lambda: None
        self.member = "member-1"

    def memberid(self) -> str:
        return self.member

    def poll(self, _timeout: float) -> FakeMessage | None:
        if self.position == len(self.log):
            self.on_idle()
            return None
        self.position += 1
        return self.log[self.position - 1]

    def seek(self, where: TopicPartition) -> None:
        self.seeks.append(where.offset)
        self.position = where.offset - self.first

    def consumer_group_metadata(self) -> str:
        return "group-metadata"

    def close(self) -> None:
        pass


class FakeProducer:
    """Records every transactional call; ``failures`` scripts what the next calls raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.failures: dict[str, list[KafkaException]] = {}
        self.open: list[bytes] = []
        self.committed: list[bytes] = []
        self.reply: threading.Event | None = None  # set to make send_offsets wait, as seen live

    def _call(self, name: str, timeout: Any = None) -> None:
        self.calls.append((name, timeout))
        queued = self.failures.get(name)
        if queued:
            raise queued.pop(0)

    def init_transactions(self) -> None:
        pass

    def begin_transaction(self) -> None:
        self._call("begin")
        self.open = []

    def produce(self, _topic: str, value: bytes, **_kwargs: Any) -> None:
        self.open.append(value)

    def send_offsets_to_transaction(self, _positions: Any, _group: Any, timeout: Any) -> None:
        self._call("send_offsets", timeout)
        if self.reply is not None:
            self.reply.wait()

    def commit_transaction(self, timeout: Any = None) -> None:
        self._call("commit", timeout)
        self.committed.extend(self.open)

    def abort_transaction(self, timeout: Any = None) -> None:
        self._call("abort", timeout)
        self.open = []

    def flush(self, _timeout: float) -> None:
        pass

    def named(self, name: str) -> list[Any]:
        return [timeout for call, timeout in self.calls if call == name]


def timed_out() -> KafkaException:
    return KafkaException(KafkaError(KafkaError._TIMED_OUT, "timed out", retriable=True))


def must_abort() -> KafkaException:
    return KafkaException(
        KafkaError(KafkaError.ILLEGAL_GENERATION, "rebalanced", txn_requires_abort=True)
    )


def echo(batch: list[Any]) -> list[OutputRecord]:
    return [OutputRecord(topic="norm.x", value=message.value()) for message in batch]


class Loop:
    """A ``TxnProcessor`` over the fakes, stopping once the consumer has nothing left."""

    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        fn: Callable[[list[Any]], list[OutputRecord]] = echo,
        instance: str = "0",
        txn_timeout_ms: int = 120_000,
    ) -> None:
        self.consumer = FakeConsumer(first=10, count=2)
        self.producer = FakeProducer()
        self.consumer_conf: dict[str, Any] = {}

        def make_consumer(*_args: Any, **conf: Any) -> FakeConsumer:
            self.consumer_conf = conf
            return self.consumer

        monkeypatch.setattr(kafka, "make_consumer", make_consumer)
        monkeypatch.setattr(kafka, "make_producer", lambda *_a, **_k: self.producer)
        self.cfg = Settings(
            _env_file=None,
            data_dir=tmp_path,
            norm_batch_max=2,
            norm_batch_ms=20,
            kafka_txn_timeout_ms=txn_timeout_ms,
        )
        self.poisoned: list[int] = []
        self.stalls: list[str] = []
        self.processor = TxnProcessor(
            name="normalizer",
            group="normalizer",
            fn=fn,
            instance=instance,
            cfg=self.cfg,
            on_poison=self.on_poison,
            on_stall=self.stalls.append,
        )
        self.consumer.on_idle = self.processor.stop

    def on_poison(self, batch: list[Any], _exc: Exception) -> list[OutputRecord]:
        self.poisoned.extend(message.offset() for message in batch)
        return [OutputRecord(topic="dlq", value=b"poison")]


def test_no_transactional_call_may_wait_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch)
    loop.processor.run()
    limit = loop.cfg.kafka_txn_timeout_ms / 1000
    assert loop.producer.named("send_offsets") == [limit]
    assert loop.producer.named("commit") == [limit]
    assert loop.producer.committed == [b"10", b"11"]


def test_a_call_that_times_out_stops_the_loop_for_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch)
    loop.producer.failures["send_offsets"] = [timed_out()]
    with pytest.raises(TxnStalled, match="cannot be completed"):
        loop.processor.run()
    assert len(loop.stalls) == 1
    assert loop.producer.committed == []
    # Kafka's failure is not the records': the restart must find no attempt held against them.
    assert loop.processor.journal.recorded() == (None, 0)


def test_a_call_that_never_returns_is_caught_by_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the load test hit: the call's own time limit was not honoured by the client."""
    loop = Loop(tmp_path, monkeypatch, txn_timeout_ms=200)
    loop.producer.reply = threading.Event()
    journal_when_called: list[Any] = []

    def replace_the_process(reason: str) -> None:
        loop.stalls.append(reason)
        journal_when_called.append(loop.processor.journal.recorded())
        loop.producer.reply.set()  # type: ignore[union-attr]  # stands in for the process ending

    loop.processor.on_stall = replace_the_process
    loop.processor.run()
    assert len(loop.stalls) == 1
    assert "outlived" in loop.stalls[0]
    assert journal_when_called == [(None, 0)], "no attempt may be held against the records"


def test_a_consumer_left_outside_its_group_is_caught_by_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Also seen live: put out of the group, it polled on and never asked to rejoin."""
    loop = Loop(tmp_path, monkeypatch, txn_timeout_ms=200)
    loop.consumer.on_idle = lambda: None
    loop.consumer.member = ""

    def replace_the_process(reason: str) -> None:
        loop.stalls.append(reason)
        loop.processor.stop()

    loop.processor.on_stall = replace_the_process
    loop.processor.run()
    assert len(loop.stalls) == 1
    assert "outside its consumer group" in loop.stalls[0]


def test_a_transaction_that_commits_in_time_does_not_trip_the_watchdog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch, txn_timeout_ms=200)
    loop.consumer.on_idle = lambda: None
    stop = threading.Timer(0.6, loop.processor.stop)  # three timeouts' worth of idling
    stop.start()
    loop.processor.run()
    stop.join()
    assert loop.producer.committed == [b"10", b"11"]
    assert loop.stalls == []


def test_an_abortable_failure_is_aborted_and_the_batch_is_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch)
    loop.producer.failures["send_offsets"] = [must_abort()]
    loop.processor.run()
    assert loop.producer.named("abort") == [loop.cfg.kafka_txn_timeout_ms / 1000]
    assert loop.consumer.seeks == [10], "the consumer must go back to the batch's first offset"
    assert loop.producer.committed == [b"10", b"11"], "exactly once, on the second attempt"


def test_kafka_failures_never_quarantine_good_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch)
    more_than_the_limit = loop.cfg.poison_max_retries + 2
    loop.producer.failures["commit"] = [must_abort() for _ in range(more_than_the_limit)]
    loop.processor.run()
    assert loop.poisoned == []
    assert loop.producer.committed == [b"10", b"11"]


def test_a_transaction_that_cannot_be_aborted_stops_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = Loop(tmp_path, monkeypatch)
    loop.producer.failures["commit"] = [must_abort()]
    loop.producer.failures["abort"] = [timed_out()]
    with pytest.raises(TxnStalled, match="cannot be aborted"):
        loop.processor.run()


def test_a_batch_whose_processing_raises_is_retried_then_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[int]] = []

    def explode(batch: list[Any]) -> list[OutputRecord]:
        seen.append([message.offset() for message in batch])
        raise ValueError("cannot parse this")

    loop = Loop(tmp_path, monkeypatch, fn=explode)
    loop.processor.run()
    assert seen == [[10, 11]] * loop.cfg.poison_max_retries, "the same batch, every attempt"
    assert loop.poisoned == [10, 11]
    assert loop.producer.committed == [b"poison"]
    assert loop.producer.named("abort") == [], "nothing was begun, so nothing needs aborting"


def test_each_instance_keeps_its_own_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = Loop(tmp_path, monkeypatch).processor.journal.path
    second = Loop(tmp_path, monkeypatch, instance="2").processor.journal.path
    assert first == tmp_path / "state" / "normalizer_inflight"
    assert second == tmp_path / "state" / "normalizer-2_inflight"


def test_the_group_spreads_every_topic_over_every_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sticky assignment only evened the count, which left one busy topic on one instance."""
    conf = Loop(tmp_path, monkeypatch).consumer_conf
    assert conf["partition.assignment.strategy"] == "roundrobin"
