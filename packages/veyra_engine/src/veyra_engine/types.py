"""The types IF-ENGINE-LIB promises. **These signatures are frozen from S0.**

Track C builds golden tests, backtests and the onboarding preview against them, and
Track B's console highlights read ``Token`` spans, so a change here breaks two tracks.
Adding fields is additive; renaming or removing is breaking (02_CONTRACTS §0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from veyra_common.models import DlqRecord

TokenKind = Literal[
    "ip",
    "ipv6",
    "port",
    "email",
    "url",
    "hostname",
    "user",
    "hash",
    "uuid",
    "timestamp",
    "int",
    "kv_value",
    "word",
    "quoted",
]


@dataclass(frozen=True, slots=True)
class Token:
    """One extracted token with its exact span in the decoded text.

    ``id`` is stable for identical text (``k1``, ``k2``, …): the LLM drafter points at
    these ids instead of writing values, which is what makes provenance guaranteed
    by construction (D10).
    """

    id: str
    value: str
    start: int
    end: int
    kind: TokenKind
    key: str | None = None


@dataclass(slots=True)
class Field:
    """A value exposed by one peel layer, with the span it came from."""

    path: str
    value: Any
    char_span: tuple[int, int] | None
    layer: str


@dataclass(slots=True)
class PeelResult:
    """What ``Engine.peel`` found: the layers applied and the fields they exposed."""

    layers: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    text_field: str | None = None
    text_span: tuple[int, int] | None = None
    depth: int = 0
    error: str | None = None


@dataclass(slots=True)
class NormResult:
    """The engine's output for one envelope.

    ``dlq`` is set for every tier >= 2 (P2: a copy, never a drop).
    """

    ocsf: dict[str, Any]
    ulpf: dict[str, Any]
    tier: int
    conformance: str
    category: str
    dlq: DlqRecord | None = None
    timings_us: dict[str, int] = field(default_factory=dict)
    parse_path: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Check:
    """One ``provenance_check`` result: does this field really point at those bytes?"""

    ocsf_path: str
    ok: bool
    reason: str = ""
    span: tuple[int, int] | None = None


@dataclass(slots=True)
class BacktestResult:
    """What a candidate contract would do to stored events (A5; C2/C4 render this)."""

    n: int = 0
    tier_before: dict[int, int] = field(default_factory=dict)
    tier_after: dict[int, int] = field(default_factory=dict)
    upgraded: int = 0
    regressed: int = 0
    unchanged: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)
    field_coverage: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class Budget:
    """A per-event time budget the pipeline stages consult (A4).

    Checking once at the end only *reports* an overrun; a pathological line has already cost the
    time by then. Stages call :meth:`expired` at their boundaries so the work stops early, which is
    what keeps a hostile event inside 2x the budget instead of 4x.
    """

    limit_us: int
    started_us: int

    def elapsed_us(self) -> int:
        from veyra_common.ids import monotonic_us

        return monotonic_us() - self.started_us

    def expired(self) -> bool:
        return self.elapsed_us() > self.limit_us

    @classmethod
    def start(cls, limit_us: int) -> Budget:
        from veyra_common.ids import monotonic_us

        return cls(limit_us=limit_us, started_us=monotonic_us())


@dataclass(slots=True)
class EngineContext:
    """Everything the engine may read. No I/O, no clock, no network (P3).

    ``vocab`` and ``enrich`` come from the compacted ``control`` topic; ``settings``
    carries the profile limits (budget, peel depth, max event bytes).
    """

    vocab: dict[str, dict[str, Any]] = field(default_factory=dict)
    enrich: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    budget_us: int = 5000
    peel_max_depth: int = 4
    max_event_bytes: int = 65536
    ocsf_version: str = "1.9.0"
