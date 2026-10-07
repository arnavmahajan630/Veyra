"""The pipeline load test's own judgement: when it is finished, and when it is stuck.

It once printed a rate of 0 for as long as it was left running, because it was waiting for a count
the stalled normalizers would never reach, and stopped outright on one unanswered question to
Kafka. These tests drive ``main`` over scripted samples, with no stack.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import load_raw


def scripted(monkeypatch: pytest.MonkeyPatch, samples: list[tuple[int, int] | None]) -> None:
    """``progress`` answers from ``samples``, then repeats the last one for good."""
    feed: Iterator[tuple[int, int] | None] = iter(samples)
    last = samples[-1]
    monkeypatch.setattr(load_raw, "progress", lambda: next(feed, last))


def run(*extra: str) -> int:
    return load_raw.main(["--measure-only", "--interval", "0.05", *extra])


def test_it_finishes_when_nothing_is_left_queued(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    scripted(monkeypatch, [(100, 50), (120, 30), (150, 0)])
    assert run() == 0
    assert "normalized 50 events" in capsys.readouterr().out


def test_a_sample_kafka_cannot_answer_is_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    scripted(monkeypatch, [(100, 50), None, None, (150, 0)])
    assert run() == 0
    out = capsys.readouterr().out
    assert out.count("Kafka did not answer") == 2
    assert "normalized 50 events" in out


def test_a_pipeline_that_stops_moving_is_reported_not_watched_forever(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    scripted(monkeypatch, [(100, 900), (130, 870)])
    assert run("--stall", "0.2") == 1
    out = capsys.readouterr().out
    assert "nothing was normalized for 0 s with 870 still queued" in out
    assert "logs normalizer" in out


def test_kafka_going_silent_for_good_still_ends_at_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scripted(monkeypatch, [(100, 900), None])
    assert run("--timeout", "0.3", "--stall", "60") == 0  # --measure-only never fails a run


def test_no_kafka_at_the_start_is_said_plainly(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    scripted(monkeypatch, [None])
    assert run() == 1
    assert "Kafka is not answering" in capsys.readouterr().out


def test_a_slow_first_answer_does_not_stop_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    scripted(monkeypatch, [None, None, (100, 50), (150, 0)])
    assert run() == 0
    assert "normalized 50 events" in capsys.readouterr().out


class FinishedProducer:
    """Stands in for a producer process that has already sent its share and reported it."""

    def __init__(self, *, target: Any, args: tuple[int, int, int, Any], daemon: bool) -> None:
        self.count, _seed, _buckets, self.done = args

    def start(self) -> None:
        self.done.put((self.count, 0))

    def is_alive(self) -> bool:
        return False


def test_other_traffic_arriving_does_not_keep_a_finished_run_open(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    """The demo baseline keeps writing to raw.*, so the queue may never be seen empty."""
    monkeypatch.setattr(load_raw.mp, "Process", FinishedProducer)
    scripted(monkeypatch, [(100, 0), (140, 270), (160, 250), (190, 250)])
    assert load_raw.main(["--events", "60", "--workers", "2", "--interval", "0.05"]) == 0
    out = capsys.readouterr().out
    assert "produced 60 envelopes" in out
    assert "normalized 60 events" in out
