"""Contract lint (C2): mistakes the compiler accepts but a reviewer should see.

Errors block a submission (the version stays a draft); warnings are shown beside it.

- ``shadowed`` (error): an earlier template matches a later template's example line, so
  the engine (first match wins) would give that line to the earlier template.
- ``required_unproduced`` (error): a ``required`` path no template produces, so every event
  would be tier 2.
- ``required_missing`` (warning): one template does not produce a ``required`` path; its
  events will be tier 2.
- ``pii_unmapped`` (warning): a ``pii`` path no template maps.
- ``pii_undeclared`` (warning): a template maps a personal-data path the contract does not
  list under ``pii``, so masking routes would pass it through.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import re2

from veyra_contracts.compiler import CompiledContract
from veyra_contracts.models import ContractYaml
from veyra_contracts.pattern import example_text

# The engine always fills `time` (falling back to arrival time), so it is always produced.
ALWAYS_PRODUCED = frozenset({"time"})
PII_FIELDS = frozenset(
    {"user.name", "user.uid", "user.domain", "actor.user.name", "src_endpoint.ip"}
)


@dataclass(frozen=True)
class LintFinding:
    level: Literal["error", "warning"]
    code: str
    message: str
    template: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def has_errors(findings: list[LintFinding]) -> bool:
    return any(f.level == "error" for f in findings)


def lint(spec: ContractYaml, compiled: CompiledContract) -> list[LintFinding]:
    findings: list[LintFinding] = []
    findings.extend(_shadowed(spec, compiled))
    produced = {t.id: {e.ocsf_path for e in t.map} | ALWAYS_PRODUCED for t in compiled.templates}
    everything = set().union(*produced.values()) if produced else set(ALWAYS_PRODUCED)
    for path in compiled.required:
        if path not in everything:
            findings.append(
                LintFinding("error", "required_unproduced", f"no template produces {path}")
            )
            continue
        for template_id, paths in produced.items():
            if path not in paths:
                findings.append(
                    LintFinding(
                        "warning",
                        "required_missing",
                        f"{template_id} does not produce {path}; its events will be tier 2",
                        template_id,
                    )
                )
    declared = set(compiled.pii)
    for path in compiled.pii:
        if path not in everything:
            findings.append(LintFinding("warning", "pii_unmapped", f"no template maps {path}"))
    for template_id, paths in produced.items():
        for path in sorted((paths & PII_FIELDS) - declared):
            findings.append(
                LintFinding(
                    "warning",
                    "pii_undeclared",
                    f"{template_id} maps {path}, which is not listed under pii",
                    template_id,
                )
            )
    return findings


def _shadowed(spec: ContractYaml, compiled: CompiledContract) -> list[LintFinding]:
    regexes = [re2.compile(t.regex) for t in compiled.templates]
    out: list[LintFinding] = []
    for later, template in enumerate(spec.templates):
        example = example_text(template.pattern)
        if not regexes[later].search(example):
            continue  # no reliable example for this pattern; say nothing rather than guess
        for earlier in range(later):
            if regexes[earlier].search(example):
                winner = spec.templates[earlier].id
                out.append(
                    LintFinding(
                        "error",
                        "shadowed",
                        f"{template.id} never matches: {winner} comes first and also matches "
                        f"its lines, e.g. {example!r}",
                        template.id,
                    )
                )
                break
    return out
