"""Golden tests: run a contract's ``tests[]`` through the real engine (C2).

Each test pairs a sample file (the raw bytes of one event) with an expected file (the
normalized IF-NORM-EVENT). The runner stamps the sample into an envelope, normalizes it
with ``veyra_engine`` — the same library the normalizer runs — and reports every path
where the output differs, ignoring the fields that change on every run.

Determinism: the envelope's ``event_uid`` and ``received_time`` are fixed, so a sample
always normalizes to the same bytes. A sample may override the arrival time with a first
line ``#! received_time=<RFC3339>`` (year inference reads it); that line is not part of
the event.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_contracts.compiler import CompiledContract
from veyra_contracts.models import ContractYaml
from veyra_engine import Engine, EngineContext, serialize, validate_event

GOLDEN_SOURCE = "src_golden"
GOLDEN_RECEIVED = "2026-09-26T14:10:00.000000000Z"
HEADER = b"#! received_time="
# Change on every run or depend on where the event came from, not on the contract.
IGNORED = frozenset({"ulpf.event_uid", "ulpf.raw_ref", "ulpf.engine_version"})


@dataclass(frozen=True)
class GoldenCase:
    sample: str
    expect: str
    ok: bool
    tier: int | None = None
    diffs: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class GoldenReport:
    contract: str
    version: int
    cases: list[GoldenCase]

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def failed(self) -> int:
        return sum(1 for case in self.cases if not case.ok)

    @property
    def passed(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "version": self.version,
            "passed": self.passed,
            "total": self.total,
            "failed": self.failed,
            "cases": [asdict(case) for case in self.cases],
        }


def split_header(data: bytes) -> tuple[bytes, str]:
    """Return ``(raw_event_bytes, received_time)`` for one sample file's contents."""
    received = GOLDEN_RECEIVED
    if data.startswith(HEADER):
        line, _, data = data.partition(b"\n")
        received = line[len(HEADER) :].decode("ascii").strip()
    if data.endswith(b"\r\n"):
        data = data[:-2]
    elif data.endswith(b"\n"):
        data = data[:-1]
    return data, received


def golden_envelope(
    raw: bytes, compiled: CompiledContract, *, received_time: str, index: int = 0
) -> Envelope:
    return stamp(
        raw,
        collector_id="golden",
        transport="syslog_udp",
        framing_method="datagram",
        zone="core",
        tenant_id=compiled.tenant,
        source_id=GOLDEN_SOURCE,
        vendor=compiled.contract,
        event_uid=f"00000000-0000-7000-8000-{index:012d}",
        received_time=received_time,
    )


def golden_engine(compiled: CompiledContract, ctx: EngineContext | None = None) -> Engine:
    """An engine with only this contract loaded, bound to the golden source."""
    engine = Engine(ctx or EngineContext())
    document = compiled.to_dict()
    document["sources"] = [GOLDEN_SOURCE]
    engine.load([document])
    return engine


def normalize_sample(
    engine: Engine, compiled: CompiledContract, data: bytes, *, index: int = 0
) -> tuple[dict[str, Any], int]:
    raw, received = split_header(data)
    result = engine.normalize(golden_envelope(raw, compiled, received_time=received, index=index))
    event: dict[str, Any] = json.loads(serialize(result.ocsf))
    return event, result.tier


def diff_paths(expected: Any, actual: Any, ignore: frozenset[str] = IGNORED) -> list[str]:
    """Every leaf path where the two JSON values differ, sorted (``a.b[0].c``)."""
    out: list[str] = []
    _diff(expected, actual, "", ignore, out)
    return sorted(out)


def _diff(expected: Any, actual: Any, path: str, ignore: frozenset[str], out: list[str]) -> None:
    if path in ignore:
        return
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = f"{path}.{key}" if path else str(key)
            if key not in expected or key not in actual:
                if child not in ignore:
                    out.append(child)
                continue
            _diff(expected[key], actual[key], child, ignore, out)
        return
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append(f"{path}[len {len(expected)} != {len(actual)}]")
            return
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            _diff(e, a, f"{path}[{i}]", ignore, out)
        return
    if type(expected) is not type(actual) or expected != actual:
        out.append(path or "<root>")


def run_golden(
    spec: ContractYaml,
    compiled: CompiledContract,
    base_dir: Path,
    *,
    ctx: EngineContext | None = None,
    update: bool = False,
) -> GoldenReport:
    """Run every ``tests[]`` pair; with ``update``, (re)write the expected files instead."""
    engine = golden_engine(compiled, ctx)
    cases: list[GoldenCase] = []
    for index, test in enumerate(spec.tests):
        sample_path, expect_path = base_dir / test.sample, base_dir / test.expect
        if not sample_path.is_file():
            cases.append(GoldenCase(test.sample, test.expect, ok=False, error="sample missing"))
            continue
        event, tier = normalize_sample(engine, compiled, sample_path.read_bytes(), index=index)
        # A schema violation fails the case; a missing required path already shows as a
        # tier difference against the expected file.
        schema_errors = list(validate_event(event).errors)
        if update:
            expect_path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(event, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            expect_path.write_text(text, encoding="utf-8", newline="\n")
        if not expect_path.is_file():
            cases.append(
                GoldenCase(test.sample, test.expect, ok=False, tier=tier, error="expected missing")
            )
            continue
        expected = json.loads(expect_path.read_text(encoding="utf-8"))
        diffs = diff_paths(expected, event)
        cases.append(
            GoldenCase(
                test.sample,
                test.expect,
                ok=not diffs and not schema_errors,
                tier=tier,
                diffs=diffs,
                schema_errors=schema_errors,
            )
        )
    return GoldenReport(contract=compiled.contract, version=compiled.version, cases=cases)
