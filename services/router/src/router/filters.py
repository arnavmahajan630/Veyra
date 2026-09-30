"""Which events a route accepts (IF-ROUTES ``filter``).

Everything a filter reads comes off ``ulpf``, which A3 guarantees is complete on **every** event
including tier 4 — the alternative, reading OCSF fields, would silently drop the unparseable events
that are exactly what an operator most wants to see arrive somewhere.

An absent key means "no constraint", so ``{}`` accepts everything and
``{"tiers": [1]}`` constrains the tier only. ``tenants: ["*"]`` is the explicit wildcard IF-ROUTES
uses for the Wazuh route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

WILDCARD = "*"


@dataclass(frozen=True, slots=True)
class Filter:
    """A compiled IF-ROUTES filter. Compiled once at load, evaluated per event."""

    tenants: frozenset[str] | None = None
    tiers: frozenset[int] | None = None
    classes: frozenset[int] | None = None
    sources: frozenset[str] | None = None

    @classmethod
    def parse(cls, spec: dict[str, Any]) -> Filter:
        unknown = set(spec) - {"tenants", "tiers", "classes", "sources"}
        if unknown:
            # Refused at load, not per event: a typo'd key that silently matched everything would
            # send one tenant's events to another tenant's partner feed.
            raise ValueError(f"unknown filter key(s): {', '.join(sorted(unknown))}")
        return cls(
            tenants=_strings(spec.get("tenants")),
            tiers=_ints(spec.get("tiers")),
            classes=_ints(spec.get("classes")),
            sources=_strings(spec.get("sources")),
        )

    def accepts(self, event: dict[str, Any]) -> bool:
        ulpf = event.get("ulpf") or {}
        if (
            self.tenants is not None
            and WILDCARD not in self.tenants
            and str(ulpf.get("tenant_id", "")) not in self.tenants
        ):
            return False
        if self.tiers is not None and int(ulpf.get("tier", 0)) not in self.tiers:
            return False
        if self.classes is not None and int(event.get("class_uid", 0)) not in self.classes:
            return False
        return not (
            self.sources is not None
            and WILDCARD not in self.sources
            and str(ulpf.get("source_id", "")) not in self.sources
        )


def _strings(values: Any) -> frozenset[str] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise ValueError(f"expected a list, got {type(values).__name__}")
    return frozenset(str(value) for value in values)


def _ints(values: Any) -> frozenset[int] | None:
    if values is None:
        return None
    if not isinstance(values, list):
        raise ValueError(f"expected a list, got {type(values).__name__}")
    try:
        return frozenset(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected integers, got {values!r}") from exc
