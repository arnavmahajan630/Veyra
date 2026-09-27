"""Follow the compacted ``control`` topic and keep the engine's contract set current.

Every consumer of ``control`` works the same way (IF-CONTROL): read the topic from the
beginning at startup to rebuild state, then keep following it. Two details matter here:

* **readiness** is not signalled until that first full read completes, because a normalizer with
  an empty contract set would turn every event into tier 4 and quietly poison the pipeline —
  the failure mode S1 lists first under "events on raw.* but nothing on norm.*";
* **swaps are atomic**: a new contract set is built, then handed to ``Engine.load`` in one call,
  so no event is ever normalized against half a set.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from confluent_kafka import KafkaError, KafkaException

from veyra_common.kafka import make_consumer
from veyra_common.models import (
    KEY_PREFIX_CONTRACT,
    KEY_PREFIX_ENRICH,
    KEY_PREFIX_SOURCE,
    KEY_PREFIX_VOCAB,
)
from veyra_common.settings import Settings
from veyra_common.topics import TOPIC_CONTROL
from veyra_engine import Engine

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ControlState:
    """What the control topic currently says."""

    contracts: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    vocab: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrich: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    updates: int = 0


class ControlFollower:
    """Background reader that applies `control` to an :class:`Engine`."""

    def __init__(
        self,
        engine: Engine,
        *,
        cfg: Settings,
        group: str,
        idle_ms: int = 1500,
        on_change: Any = None,
    ) -> None:
        self.engine = engine
        self.cfg = cfg
        self.group = group
        self.idle_ms = idle_ms
        self.on_change = on_change
        self.state = ControlState()
        self.ready = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="control-follower", daemon=True)
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
        consumer = make_consumer(
            self.group, [TOPIC_CONTROL], cfg=self.cfg, auto_offset_reset="earliest"
        )
        last_message = time.monotonic()
        # How far we must read before the rebuild is complete, filled once the consumer has an
        # assignment. Until then, "no messages" means "not subscribed yet", NOT "topic is empty":
        # treating those as the same made a restarted normalizer declare itself ready with zero
        # contracts and turn every event into tier 4 — the first failure mode S1 lists.
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
                        self._apply()
                        self.ready.set()
                        log.info(
                            "control rebuilt",
                            extra={
                                "contracts": len(self.state.contracts),
                                "sources": len(self.state.sources),
                                "vocab": len(self.state.vocab),
                            },
                        )
                    continue
                if message.error():
                    if message.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(message.error())

                last_message = time.monotonic()
                self._handle(message.key(), message.value())
                if self.ready.is_set():
                    # Already serving: apply each change as it arrives.
                    self._apply()
                elif self._caught_up(consumer, targets, last_message):
                    # The last message of the backlog: rebuild is done, open the gate.
                    self._apply()
                    self.ready.set()
                    log.info(
                        "control rebuilt",
                        extra={
                            "contracts": len(self.state.contracts),
                            "sources": len(self.state.sources),
                            "vocab": len(self.state.vocab),
                        },
                    )
        except Exception:
            log.exception("control follower stopped")
        finally:
            consumer.close()

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
        """Have we read the whole compacted backlog?

        True when every assigned partition's position has reached the high watermark we saw at
        startup. The idle timer is only a fallback for a genuinely empty topic, and it does not
        fire until an assignment exists.
        """
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

    def _handle(self, key: bytes | None, value: bytes | None) -> None:
        if not key:
            return
        name = key.decode("utf-8", errors="replace")
        payload: dict[str, Any] | None = None
        if value is not None:
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                log.error("control message is not JSON", extra={"key": name})
                return

        with self._lock:
            if name.startswith(KEY_PREFIX_CONTRACT):
                contract_id = name.removeprefix(KEY_PREFIX_CONTRACT)
                if payload is None:  # a tombstone retires the contract
                    self.state.contracts.pop(contract_id, None)
                    self.state.candidates.pop(contract_id, None)
                    return
                compiled = payload.get("compiled")
                if compiled:
                    self.state.contracts[contract_id] = compiled
                candidate = payload.get("candidate")
                if candidate and candidate.get("compiled"):
                    self.state.candidates[contract_id] = candidate["compiled"]
                else:
                    self.state.candidates.pop(contract_id, None)

            elif name.startswith(KEY_PREFIX_SOURCE):
                source_id = name.removeprefix(KEY_PREFIX_SOURCE)
                if payload is None:
                    self.state.sources.pop(source_id, None)
                else:
                    self.state.sources[source_id] = payload

            elif name.startswith(KEY_PREFIX_VOCAB):
                vocab_name = name.removeprefix(KEY_PREFIX_VOCAB)
                if payload is None:
                    self.state.vocab.pop(vocab_name, None)
                else:
                    self.state.vocab[vocab_name] = payload

            elif name.startswith(KEY_PREFIX_ENRICH):
                table = name.removeprefix(KEY_PREFIX_ENRICH)
                if payload is None:
                    self.state.enrich.pop(table, None)
                else:
                    self.state.enrich[table] = list(payload.get("rows") or [])

            self.state.updates += 1

    def _apply(self) -> None:
        """Hand the current state to the engine in one atomic swap."""
        with self._lock:
            contracts = list(self.state.contracts.values())
            candidates = dict(self.state.candidates)
            vocab = dict(self.state.vocab)
            enrich = dict(self.state.enrich)
            sources = dict(self.state.sources)

        # A source may name a contract that does not list it (IF-CONTROL keeps both sides), so
        # the source records are folded in — otherwise a freshly onboarded source would be
        # normalized as if it had no contract.
        by_contract: dict[str, list[str]] = {}
        for source_id, record in sources.items():
            contract_id = record.get("contract_id")
            if contract_id:
                by_contract.setdefault(str(contract_id), []).append(source_id)
        for contract in contracts:
            extra = by_contract.get(str(contract.get("contract")), [])
            declared = list(contract.get("sources") or [])
            merged = sorted(set(declared) | set(extra))
            if merged != declared:
                contract["sources"] = merged

        self.engine.ctx.vocab = vocab
        self.engine.ctx.enrich = enrich
        self.engine.load(contracts)
        for contract_id, compiled in candidates.items():
            self.engine.set_candidate(compiled, contract_id)
        if self.engine.load_errors:
            log.error("contracts with errors", extra={"errors": self.engine.load_errors[:5]})
        if self.on_change is not None:
            self.on_change(self.state)
