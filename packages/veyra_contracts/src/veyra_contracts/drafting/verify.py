"""Mechanical checks on a drafted contract, before any human looks at it (C4 "Verification").

1. Compile it (C2). A failure stops here, with its line and column.
2. Normalize every sample with the same engine binding the golden runner uses.
3. Provenance, per mapped path of the drafted templates: a token-mapped path must be
   *located* (in ``ulpf.field_offsets``) and ``provenance_check`` must confirm the bytes;
   a constant or ``$__text`` is *derived* and says so. A sample the template doesn't match
   is reported against every row, rather than silently skipped.
4. Type checks: IP paths hold IPs, ports are 0–65535, and ``time`` came from the event.

Backtesting against stored events needs the event index, so control-api does it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from veyra_contracts.compiler import compile
from veyra_contracts.errors import ContractError
from veyra_contracts.golden import GOLDEN_RECEIVED, golden_engine, golden_envelope
from veyra_engine import EngineContext, provenance_check

IP_PATHS = frozenset({"src_endpoint.ip", "dst_endpoint.ip", "device.ip"})
PORT_PATHS = frozenset({"src_endpoint.port", "dst_endpoint.port"})


@dataclass
class Row:
    ocsf_path: str
    ok: bool
    reason: str = ""
    kind: str = "located"  # located | derived


@dataclass
class Verification:
    compile_error: dict[str, Any] | None = None
    tiers: dict[str, int] = field(default_factory=dict)
    provenance: list[Row] = field(default_factory=list)
    type_issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.compile_error is None and all(row.ok for row in self.provenance) and not self.type_issues

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"ok": self.ok}


def _dig(event: dict[str, Any], path: str) -> Any:
    cursor: Any = event
    for part in path.split("."):
        cursor = cursor.get(part) if isinstance(cursor, dict) else None
    return cursor


def _type_issue(path: str, value: Any) -> str | None:
    if path in IP_PATHS:
        try:
            ipaddress.ip_address(str(value))
        except ValueError:
            return f"{path} = {value!r} is not an IP address"
    if path in PORT_PATHS and not (isinstance(value, int) and 0 <= value <= 65535):
        return f"{path} = {value!r} is not a port"
    return None


def verify(
    contract_yaml: str,
    samples: Sequence[bytes],
    template_ids: set[str],
    *,
    ctx: EngineContext | None = None,
) -> Verification:
    result = Verification()
    try:
        compiled = compile(contract_yaml)
    except ContractError as exc:
        result.compile_error = {"message": exc.message, "line": exc.line, "column": exc.column}
        return result
    templates = {t.id: t for t in compiled.templates if t.id in template_ids}
    paths = {e.ocsf_path: e.kind for t in templates.values() for e in t.map}
    failures: dict[str, list[str]] = {path: [] for path in paths}
    engine = golden_engine(compiled, ctx)
    has_time_block = bool(compiled.time.get("field"))
    for index, raw in enumerate(samples):
        normalized = engine.normalize(
            golden_envelope(raw, compiled, received_time=GOLDEN_RECEIVED, index=index)
        )
        tier = str(normalized.tier)
        result.tiers[tier] = result.tiers.get(tier, 0) + 1
        template = (normalized.ulpf.get("template") or {}).get("id")
        if template not in templates:
            for path in paths:
                failures[path].append(f"sample {index + 1} did not match the drafted template")
            continue
        event = normalized.ocsf
        offsets = normalized.ulpf.get("field_offsets") or {}
        checks = {c.ocsf_path: c for c in provenance_check(event, raw)}
        for path, kind in paths.items():
            if kind in ("const", "text"):
                continue
            check = checks.get(path)
            if path not in offsets or check is None:
                failures[path].append(f"sample {index + 1}: {path} has no byte span")
            elif not check.ok:
                failures[path].append(f"sample {index + 1}: {check.reason}")
            issue = _type_issue(path, _dig(event, path))
            if issue and issue not in result.type_issues:
                result.type_issues.append(issue)
        if has_time_block and (normalized.ulpf.get("time") or {}).get("source") != "event":
            issue = "time did not parse from the event; the arrival time was used"
            if issue not in result.type_issues:
                result.type_issues.append(issue)
    for path, kind in sorted(paths.items()):
        derived = kind in ("const", "text")
        reasons = failures[path]
        result.provenance.append(
            Row(
                ocsf_path=path,
                ok=not reasons,
                reason="; ".join(reasons) or ("derived: " + kind if derived else ""),
                kind="derived" if derived else "located",
            )
        )
    return result
