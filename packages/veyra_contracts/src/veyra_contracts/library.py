"""Library matching (C3): which library pack already parses these samples?

Every pack in the registry's ``library/`` directory is compiled and run over the samples
with the same engine the normalizer uses (bound to the golden source, like golden tests).
A pack *matches* when at least ``threshold`` of the samples reach tier 1; onboarding then
proposes cloning the pack instead of drafting a new contract (C4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from veyra_contracts.compiler import CompiledContract, compile
from veyra_contracts.errors import ContractError
from veyra_contracts.golden import GOLDEN_RECEIVED, golden_engine, golden_envelope
from veyra_engine import EngineContext

LIBRARY_DIR = "library"


@dataclass(frozen=True)
class LibraryMatch:
    contract_id: str
    path: str
    tier1_pct: float
    tier2_pct: float
    matched: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LibraryPack:
    path: str  # relative to the registry root
    compiled: CompiledContract


def load_library(registry: Path) -> list[LibraryPack]:
    """Every pack under ``<registry>/library/``; one that does not compile is skipped
    (``make contracts-test`` is where it fails loudly)."""
    packs: list[LibraryPack] = []
    for path in sorted((registry / LIBRARY_DIR).glob("*.yaml")):
        try:
            compiled = compile(path.read_text(encoding="utf-8"))
        except ContractError:
            continue
        packs.append(LibraryPack(path.relative_to(registry).as_posix(), compiled))
    return packs


def library_match(
    samples: Sequence[str | bytes],
    packs: Sequence[LibraryPack],
    *,
    threshold: float,
    ctx: EngineContext | None = None,
) -> list[LibraryMatch]:
    """Rank the packs by how many samples each takes to tier 1 (then tier 2)."""
    if not samples:
        return []
    raws = [s.encode("utf-8") if isinstance(s, str) else s for s in samples]
    out: list[LibraryMatch] = []
    for pack in packs:
        engine = golden_engine(pack.compiled, ctx)
        tiers = [
            engine.normalize(
                golden_envelope(raw, pack.compiled, received_time=GOLDEN_RECEIVED, index=i)
            ).tier
            for i, raw in enumerate(raws)
        ]
        tier1 = tiers.count(1) / len(tiers)
        tier2 = tiers.count(2) / len(tiers)
        out.append(
            LibraryMatch(
                contract_id=pack.compiled.contract,
                path=pack.path,
                tier1_pct=round(tier1, 3),
                tier2_pct=round(tier2, 3),
                matched=tier1 >= threshold,
            )
        )
    return sorted(out, key=lambda m: (-m.tier1_pct, -m.tier2_pct, m.contract_id))
