"""Keep the engine's contract set current from the compacted ``control`` topic.

The loop, the watermark-based readiness rule and the tombstone semantics live in
:class:`veyra_common.control.ControlReader`, because the gateway (A2) and the router (A6) follow the
same topic and the readiness rule is the part that is expensive to get wrong. What stays here is the
normalizer's own half:

* **what to keep** — contracts, candidates, sources, vocab and enrich tables;
* **swaps are atomic** — a new contract set is built, then handed to ``Engine.load`` in one call, so
  no event is ever normalized against half a set;
* **readiness matters more here than anywhere else** — a normalizer serving with an empty contract
  set turns every event into tier 4 and quietly poisons the pipeline.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from veyra_common.control import ControlReader
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
    """Applies `control` to an :class:`Engine`."""

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
        self.on_change = on_change
        self.state = ControlState()
        self._lock = threading.Lock()
        self._reader = ControlReader(
            group=group,
            cfg=cfg,
            prefixes=(
                KEY_PREFIX_CONTRACT,
                KEY_PREFIX_SOURCE,
                KEY_PREFIX_VOCAB,
                KEY_PREFIX_ENRICH,
            ),
            on_message=self._handle,
            on_change=self._apply,
            idle_ms=idle_ms,
            # `make_consumer` is resolved from this module at call time, so a test can patch
            # `normalizer.control.make_consumer` and get its fake.
            consumer_factory=lambda: make_consumer(
                group, [TOPIC_CONTROL], cfg=cfg, auto_offset_reset="earliest"
            ),
        )

    # ---------------------------------------------------------------- lifecycle
    @property
    def ready(self) -> threading.Event:
        return self._reader.ready

    def start(self) -> None:
        self._reader.start()

    def stop(self) -> None:
        self._reader.stop()

    def wait_ready(self, timeout: float) -> bool:
        """Block until the first full read of ``control`` is done."""
        return self._reader.wait_ready(timeout)

    # ---------------------------------------------------------------- state
    def _handle(self, name: str, payload: dict[str, Any] | None) -> None:
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
