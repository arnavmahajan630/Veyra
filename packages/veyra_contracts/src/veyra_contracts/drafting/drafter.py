"""Where a draft comes from (C4 modes), with its badge and the heuristic cross-check."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from veyra_contracts.drafting.cache import DraftCache
from veyra_contracts.drafting.heuristic import heuristic
from veyra_contracts.drafting.ollama import DraftFailed
from veyra_contracts.drafting.prompt import messages, prompt_sha
from veyra_contracts.drafting.request import Prepared
from veyra_contracts.drafting.schema import DraftResponse, problems

MODES = ("live", "cache", "live_then_cache", "heuristic")


class Model(Protocol):
    model: str

    def draft(self, prepared: Prepared) -> tuple[DraftResponse, str]: ...


@dataclass
class Outcome:
    response: DraftResponse
    source: str
    latency_ms: float
    review: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _targets(response: DraftResponse) -> dict[str, str]:
    return {m.ocsf_path: m.token or f"const:{m.const}" for m in response.mappings}


def disagreements(response: DraftResponse, baseline: DraftResponse) -> list[str]:
    """Paths both drafters map, but to different tokens or constants."""
    ours, theirs = _targets(response), _targets(baseline)
    return sorted(path for path in ours.keys() & theirs.keys() if ours[path] != theirs[path])


class Drafter:
    def __init__(
        self,
        *,
        mode: str,
        cache: DraftCache,
        client: Model | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.mode = mode
        self.cache = cache
        self.client = client
        self._clock = clock

    def _live(self, prepared: Prepared, notes: list[str]) -> Outcome | None:
        if self.client is None:
            notes.append("no model configured")
            return None
        try:
            response, _raw = self.client.draft(prepared)
        except DraftFailed as exc:
            notes.append(f"live draft failed: {exc}")
            return None
        return Outcome(response, f"llm:{self.client.model}", 0.0)

    def _cached(self, prepared: Prepared, notes: list[str]) -> Outcome | None:
        entry = self.cache.get(prepared.template_sig)
        if entry is None:
            notes.append("no cached draft for this shape")
            return None
        response = DraftResponse.model_validate(entry.response)
        found = problems(response, {t["id"] for t in prepared.request["tokens"]})
        if found:
            notes.append(f"cached draft no longer fits these samples: {'; '.join(found)}")
            return None
        if entry.prompt_sha != prompt_sha(messages(prepared.request)):
            notes.append("cached draft was made from a different prompt")
        if self.client is not None and entry.model != self.client.model:
            notes.append(f"cached draft is from {entry.model}, not {self.client.model}")
        return Outcome(response, f"cache:{entry.model}", 0.0)

    def draft(self, prepared: Prepared, *, mode: str | None = None) -> Outcome:
        mode = mode or self.mode
        started = self._clock()
        notes: list[str] = []
        outcome: Outcome | None = None
        if mode in ("live", "live_then_cache"):
            outcome = self._live(prepared, notes)
            if outcome is not None and mode == "live_then_cache" and self.client is not None:
                self.cache.put(
                    prepared.template_sig,
                    outcome.response.dump(),
                    model=self.client.model,
                    prompt_sha=prompt_sha(messages(prepared.request)),
                    created_at=datetime.now(UTC).isoformat(),
                )
        if outcome is None and mode in ("cache", "live_then_cache"):
            outcome = self._cached(prepared, notes)
        baseline = heuristic(prepared)
        if outcome is None:
            outcome = Outcome(baseline, "heuristic", 0.0)
        else:
            outcome.review = disagreements(outcome.response, baseline)
        outcome.notes = notes
        outcome.latency_ms = round((self._clock() - started) * 1000, 1)
        return outcome
