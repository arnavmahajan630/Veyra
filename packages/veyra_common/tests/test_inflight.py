"""The crash-loop guard's memory (A4).

These tests exercise the case that in-memory retry counting cannot cover: the process dying rather
than raising. A fresh :class:`InflightJournal` stands in for a restart, because from the journal's
point of view that is exactly what a restart is.
"""

from __future__ import annotations

from pathlib import Path

from veyra_common.inflight import InflightJournal
from veyra_common.kafka import TxnProcessor


class FakeMessage:
    """Only the four accessors the batch identity needs."""

    def __init__(self, topic: str, partition: int, offset: int) -> None:
        self._topic, self._partition, self._offset = topic, partition, offset

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return self._partition

    def offset(self) -> int:
        return self._offset


def journal(tmp_path: Path, *, max_attempts: int = 3) -> InflightJournal:
    return InflightJournal(path=tmp_path / "normalizer_inflight", max_attempts=max_attempts)


def test_batch_id_is_the_offset_range_per_partition() -> None:
    batch = [
        FakeMessage("raw.acme", 0, 7),
        FakeMessage("raw.acme", 0, 9),
        FakeMessage("raw.acme", 1, 2),
        FakeMessage("raw.authsrv", 0, 5),
    ]
    assert TxnProcessor.batch_id(batch) == (  # type: ignore[arg-type]
        ("raw.acme", 0, 7, 9),
        ("raw.acme", 1, 2, 2),
        ("raw.authsrv", 0, 5, 5),
    )


def test_batch_id_is_order_independent() -> None:
    a = [FakeMessage("raw.x", 0, 1), FakeMessage("raw.x", 0, 4)]
    b = [FakeMessage("raw.x", 0, 4), FakeMessage("raw.x", 0, 1)]
    assert TxnProcessor.batch_id(a) == TxnProcessor.batch_id(b)  # type: ignore[arg-type]


def test_first_attempt_is_one_and_nothing_is_poisonous_yet(tmp_path: Path) -> None:
    book = journal(tmp_path)
    batch = (("raw.x", 0, 1, 1),)
    assert not book.poisonous(batch)
    assert book.begin(batch) == 1
    assert not book.poisonous(batch)


def test_the_count_survives_a_restart(tmp_path: Path) -> None:
    """The whole point: a process that dies twice must not get a third clean slate."""
    batch = (("raw.x", 0, 1, 1),)
    assert journal(tmp_path).begin(batch) == 1  # crash
    assert journal(tmp_path).begin(batch) == 2  # crash
    assert journal(tmp_path).begin(batch) == 3
    assert journal(tmp_path).poisonous(batch), "third attempt should mark the batch poisonous"


def test_a_different_batch_starts_its_own_count(tmp_path: Path) -> None:
    book = journal(tmp_path)
    poison = (("raw.x", 0, 1, 1),)
    book.begin(poison)
    book.begin(poison)
    other = (("raw.x", 0, 2, 2),)
    assert book.begin(other) == 1
    assert not book.poisonous(other)
    # The old count is gone, which is correct: we got past it.
    assert not book.poisonous(poison)


def test_a_successful_commit_clears_the_journal(tmp_path: Path) -> None:
    book = journal(tmp_path)
    batch = (("raw.x", 0, 1, 1),)
    book.begin(batch)
    book.begin(batch)
    book.clear()
    assert book.recorded() == (None, 0)
    assert book.begin(batch) == 1


def test_clearing_twice_is_harmless(tmp_path: Path) -> None:
    book = journal(tmp_path)
    book.clear()
    book.clear()


def test_a_corrupt_journal_does_not_stop_the_service(tmp_path: Path) -> None:
    """A half-written or hand-edited file degrades to the in-memory limit, it never raises."""
    book = journal(tmp_path)
    book.path.parent.mkdir(parents=True, exist_ok=True)
    book.path.write_text("{not json at all")
    assert book.recorded() == (None, 0)
    assert book.begin((("raw.x", 0, 1, 1),)) == 1

    book.path.write_text('{"batch": "nonsense", "attempts": "many"}')
    assert book.recorded() == (None, 0)


def test_the_journal_is_created_under_state_dir(tmp_path: Path) -> None:
    from veyra_common.settings import Settings

    cfg = Settings(data_dir=tmp_path)
    book = InflightJournal.for_service("normalizer", cfg=cfg)
    assert book.path == tmp_path / "state" / "normalizer_inflight"
    assert book.max_attempts == cfg.poison_max_retries
    book.begin((("raw.x", 0, 1, 1),))
    assert book.path.exists(), "begin() must create data/state/ if it is missing"
