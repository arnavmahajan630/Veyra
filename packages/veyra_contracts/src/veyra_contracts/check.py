"""``make contracts-test``: compile, lint and golden-test every contract in the registry.

    python -m veyra_contracts.check [REGISTRY] [--update]

REGISTRY defaults to ``VEYRA_CONTRACTS_REPO`` (``../contracts-repo``). Every ``*.yaml``
outside hidden directories is a contract. ``--update`` rewrites the expected files from
the current engine output; review the diff before committing it. Exit status 1 when any
contract fails to compile, is not byte-deterministic, has a lint error or fails a test.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from veyra_common.settings import settings
from veyra_contracts.compiler import compile
from veyra_contracts.errors import ContractError
from veyra_contracts.golden import GoldenReport, run_golden
from veyra_contracts.lint import LintFinding, has_errors, lint
from veyra_contracts.models import ContractYaml


@dataclass
class CheckResult:
    path: str
    error: str | None = None
    deterministic: bool = True
    findings: list[LintFinding] = field(default_factory=list)
    golden: GoldenReport | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.deterministic
            and not has_errors(self.findings)
            and (self.golden is None or self.golden.passed)
        )


def find_contracts(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.yaml")
        if not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def check_file(path: Path, root: Path, *, update: bool = False) -> CheckResult:
    result = CheckResult(path=path.relative_to(root).as_posix())
    text = path.read_text(encoding="utf-8")
    try:
        compiled = compile(text)
    except ContractError as exc:
        result.error = str(exc)
        return result
    result.deterministic = compile(text).to_json() == compiled.to_json()
    spec = ContractYaml.model_validate(yaml.safe_load(text))
    result.findings = lint(spec, compiled)
    result.golden = run_golden(spec, compiled, root, update=update)
    return result


def _describe(result: CheckResult) -> list[str]:
    status = "ok  " if result.ok else "FAIL"
    golden = result.golden
    if result.error is not None or golden is None:
        return [f"{status} {result.path}: {result.error}"]
    errors = sum(1 for f in result.findings if f.level == "error")
    head = (
        f"{status} {result.path}: golden {golden.total - golden.failed}/{golden.total}"
        f"  lint {errors} error(s) {len(result.findings) - errors} warning(s)"
    )
    if not result.deterministic:
        head += "  NOT DETERMINISTIC"
    lines = [head, *(f"     {f.level}: {f.message}" for f in result.findings)]
    for case in golden.cases:
        if not case.ok:
            detail = case.error or ", ".join(case.diffs + case.schema_errors)
            lines.append(f"     test {case.sample}: {detail}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m veyra_contracts.check")
    parser.add_argument("registry", nargs="?", type=Path, default=settings.contracts_repo)
    parser.add_argument("--update", action="store_true", help="rewrite the expected files")
    args = parser.parse_args(argv)
    root: Path = args.registry.resolve()
    if not root.is_dir():
        print(f"no contract registry at {root}; clone it there or pass its path")
        return 1
    results = [check_file(path, root, update=args.update) for path in find_contracts(root)]
    for result in results:
        print("\n".join(_describe(result)))
    failed = sum(1 for r in results if not r.ok)
    print(f"{len(results) - failed}/{len(results)} contracts ok")
    return 1 if failed or not results else 0


if __name__ == "__main__":
    sys.exit(main())
