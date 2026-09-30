"""Baseline traffic loop for the demo engine (B7).

Background traffic from the registered NTRO sources, so the Overview is alive before the
demo starts. Each stream is paced at its own events-per-second target, because preflight
asserts the baseline is within ±30% of that target and the Overview's EPS reading is one of
the first things a judge sees.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field

from demo_engine.scenario import BaselineStream
from demo_engine.senders import TrafficSender

log = logging.getLogger(__name__)


@dataclass
class StreamStats:
    sent: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def eps(self) -> float:
        elapsed = max(time.monotonic() - self.started_at, 1e-6)
        return self.sent / elapsed


class BaselineLoop:
    """One thread per stream, each pacing its own corpus at its own rate."""

    def __init__(
        self,
        streams: list[BaselineStream],
        sender: TrafficSender | None = None,
        seed: int = 0,
    ) -> None:
        self.streams = streams
        self.sender = sender or TrafficSender()
        self.seed = seed
        self.stats: dict[str, StreamStats] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._running = False

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop.clear()
        for index, stream in enumerate(self.streams):
            self.stats[stream.name] = StreamStats()
            thread = threading.Thread(
                target=self._run_stream,
                args=(stream, random.Random(self.seed + index)),
                daemon=True,
                name=f"baseline-{stream.name}",
            )
            thread.start()
            self._threads.append(thread)
        log.info("baseline started: %s", ", ".join(f"{s.name}@{s.eps:g}eps" for s in self.streams))

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2.0)
        self._threads.clear()
        log.info("baseline stopped")

    def pause(self) -> None:
        self._paused.set()
        log.info("baseline paused")

    def resume(self) -> None:
        self._paused.clear()
        # A paused stream's rate must not be judged on the gap, so the window restarts.
        for stats in self.stats.values():
            stats.started_at = time.monotonic()
            stats.sent = 0
        log.info("baseline resumed")

    def is_paused(self) -> bool:
        return self._paused.is_set()

    # ------------------------------------------------------------------ reporting
    def total_eps(self) -> float:
        return sum(stats.eps for stats in self.stats.values())

    def target_eps(self) -> float:
        return sum(stream.eps for stream in self.streams)

    def snapshot(self) -> dict[str, object]:
        return {
            "paused": self.is_paused(),
            "target_eps": round(self.target_eps(), 2),
            "actual_eps": round(self.total_eps(), 2),
            "streams": {
                name: {"sent": stats.sent, "errors": stats.errors, "eps": round(stats.eps, 2)}
                for name, stats in self.stats.items()
            },
        }

    # ------------------------------------------------------------------ the loop
    def _run_stream(self, stream: BaselineStream, rng: random.Random) -> None:
        stats = self.stats[stream.name]
        action = stream.as_action()
        interval = 1.0 / stream.eps if stream.eps > 0 else 1.0
        # Pace against a monotonic deadline, so a slow send does not make the stream drift
        # permanently behind its target rate.
        next_due = time.monotonic()

        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.2)
                next_due = time.monotonic()
                continue
            try:
                self.sender.dispatch_action({"send": {**action, "count": 1, "over_s": 0}}, rng=rng)
                stats.sent += 1
            except Exception as exc:
                stats.errors += 1
                # One noisy line per stream is enough: the stack is unchanged every time.
                if stats.errors in (1, 10, 100) or stats.errors % 1000 == 0:
                    log.warning(
                        "baseline %s send failed (%d so far): %s", stream.name, stats.errors, exc
                    )
            next_due += interval
            delay = next_due - time.monotonic()
            if delay > 0:
                self._stop.wait(delay)
            else:
                next_due = time.monotonic()
