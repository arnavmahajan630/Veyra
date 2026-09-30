"""Follow the compacted ``control`` topic. One implementation, three consumers.

IF-CONTROL says every consumer of ``control`` works the same way: read the topic from the beginning
at startup to rebuild state, then keep following it. The normalizer (contracts), the ingest gateway
(API keys and sources) and the router (routes) all need that, and the subtle part is **when to
declare readiness** — which is why this lives here rather than being written three times.

The rule, learned the hard way in A3: a poll returning nothing means "not assigned yet" far more
often than it means "the topic is empty". Treating the two as the same made a restarted normalizer
report ready with zero contracts and turn every event into tier 4 — events kept flowing, they were
just useless, which is the first entry in S1's failure-mode table. So readiness waits until every
assigned partition's position has reached the high watermark that existed at startup, and the idle
timer is only a fallback for a topic that really is empty.

A service supplies the key prefixes it cares about and a callback; this class owns the thread, the
watermarks and the tombstone semantics (a ``null`` value retires a key).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from confluent_kafka import KafkaError, KafkaException

from veyra_common.kafka import make_consumer
from veyra_common.settings import Settings, settings
from veyra_common.topics import TOPIC_CONTROL

log = logging.getLogger(__name__)

# (key, payload-or-None-for-a-tombstone) -> None. Called on the follower thread, under no lock.
OnMessage = Callable[[str, dict[str, Any] | None], None]
ConsumerFactory = Callable[..., Any]


class ControlReader:
    """Background reader for the compacted ``control`` topic."""

    def __init__(
        self,
        *,
        group: str,
        prefixes: Sequence[str],
        on_message: OnMessage,
        cfg: Settings | None = None,
        on_ready: Callable[[], None] | None = None,
        on_change: Callable[[], None] | None = None,
        idle_ms: int = 1500,
        consumer_factory: ConsumerFactory | None = None,
        name: str = "control-follower",
    ) -> None:
        self.cfg = cfg or settings
        self.group = group
        self.prefixes = tuple(prefixes)
        self.on_message = on_message
        self.on_ready = on_ready
        self.on_change = on_change
        self.idle_ms = idle_ms
        # Injected so a test can drive the loop with a fake consumer, and so a service can keep
        # `make_consumer` patchable in its own module.
        self.consumer_factory = consumer_factory or (
            lambda: make_consumer(
                group, [TOPIC_CONTROL], cfg=self.cfg, auto_offset_reset="earliest"
            )
        )
        self.name = name
        self.ready = threading.Event()
        self.updates = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)

    def wait_ready(self, timeout: float) -> bool:
        """Block until the first full read of ``control`` is done."""
        return self.ready.wait(timeout)

    # ---------------------------------------------------------------- the loop
    def _run(self) -> None:
        consumer = self.consumer_factory()
        last_message = time.monotonic()
        # How far we must read before the rebuild is complete, filled once the consumer has an
        # assignment. Until then, "no messages" means "not subscribed yet", NOT "topic is empty".
        targets: dict[int, int] | None = None
        try:
            while not self._stop.is_set():
                message = consumer.poll(0.5)

                if targets is None:
                    targets = self._end_offsets(consumer)
                    if targets:
                        log.info("control rebuild target", extra={"high_watermarks": targets})

                if message is None:
                    if not self.ready.is_set() and self._caught_up(consumer, targets, last_message):
                        self._open_the_gate()
                    continue
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(message.error())

                last_message = time.monotonic()
                mine = self._handle(message.key(), message.value())
                if not self.ready.is_set():
                    if self._caught_up(consumer, targets, last_message):
                        # The last message of the backlog: the rebuild is done.
                        self._open_the_gate()
                elif mine and self.on_change is not None:
                    # Already serving: apply each change as it arrives — but only for the keys
                    # this reader asked for. Otherwise every service rebuilds its whole state
                    # whenever any other service publishes anything, which had the router
                    # reinstalling its routes (aborting in-flight deliveries) on every apikey.
                    self.on_change()
        except Exception:
            log.exception("control follower stopped")
        finally:
            consumer.close()

    def _open_the_gate(self) -> None:
        if self.on_change is not None:
            self.on_change()
        self.ready.set()
        log.info("control rebuilt", extra={"updates": self.updates, "group": self.group})
        if self.on_ready is not None:
            self.on_ready()

    def _end_offsets(self, consumer: Any) -> dict[int, int] | None:
        """High watermark per assigned partition, or ``None`` while unassigned."""
        assignment = consumer.assignment()
        if not assignment:
            return None
        targets: dict[int, int] = {}
        for partition in assignment:
            try:
                _, high = consumer.get_watermark_offsets(partition, timeout=5, cached=False)
            except Exception:  # the broker may still be settling; retry on the next poll
                return None
            targets[partition.partition] = high
        return targets

    def _caught_up(
        self, consumer: Any, targets: dict[int, int] | None, last_message: float
    ) -> bool:
        """Have we read the whole compacted backlog?"""
        if targets is None:
            return False
        if not targets or all(high == 0 for high in targets.values()):
            # The topic really is empty: nothing to wait for beyond the quiet period.
            return (time.monotonic() - last_message) * 1000 >= self.idle_ms
        try:
            positions = consumer.position(list(consumer.assignment()))
        except Exception:
            return False
        for partition in positions:
            target = targets.get(partition.partition, 0)
            if target <= 0:
                continue
            # offset < 0 means "no position yet".
            if partition.offset is None or partition.offset < 0 or partition.offset < target:
                return False
        return True

    def _handle(self, key: bytes | None, value: bytes | None) -> bool:
        """Dispatch one message. False when it was not a key this reader cares about."""
        if not key:
            return False
        name = key.decode("utf-8", errors="replace")
        if self.prefixes and not name.startswith(self.prefixes):
            return False
        payload: dict[str, Any] | None = None
        if value is not None:
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                # One malformed message must not stop the rebuild, or a single bad publish would
                # take the whole data plane down with it.
                log.error("control message is not JSON", extra={"key": name})
                return False
        self.on_message(name, payload)
        self.updates += 1
        return True
