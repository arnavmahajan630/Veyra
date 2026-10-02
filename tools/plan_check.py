"""`make plan-check` — find plan files that are stale against 02_CONTRACTS.md.

The plan is a living document (01_TEAM_GUIDE §6.4): when a phase changes an interface it
bumps the contracts version and patches every file that references it. A file whose
``contracts: vX.Y`` header is older than the current contracts version has not been
synced yet, and a phase that depends on it must not start.

Exit codes: 0 = everything current, 1 = stale files (listed), 2 = the plan is malformed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLAN_DIR = REPO / "docs" / "plan"
CONTRACTS = PLAN_DIR / "02_CONTRACTS.md"

# "**Contract version: v1.0**" in 02_CONTRACTS.md; "contracts: v1.0" in every other file.
RE_CONTRACTS_VERSION = re.compile(r"\*\*Contract version:\s*v(\d+)\.(\d+)\*\*")
RE_HEADER = re.compile(r"^\s*contracts:\s*v(\d+)\.(\d+)\s*$", re.MULTILINE)

# Files that intentionally carry no contracts header: indexes, the changelog, the
# status board, the demo script, the profile table, templates and reports.
NO_HEADER_EXPECTED = {
    "README.md",
    "00_MASTER.md",
    "01_TEAM_GUIDE.md",
    "02_CONTRACTS.md",
    "03_INFRA_PROFILES.md",
    "04_DEMO_SCRIPT.md",
    "05_CHANGELOG.md",
    "06_STATUS_BOARD.md",
}


@dataclass(frozen=True)
class PlanFile:
    path: Path
    version: tuple[int, int] | None

    @property
    def rel(self) -> str:
        return str(self.path.relative_to(REPO))


def current_version() -> tuple[int, int]:
    """The contracts version 02_CONTRACTS.md declares."""
    if not CONTRACTS.exists():
        print(f"plan-check: {CONTRACTS} is missing", file=sys.stderr)
        raise SystemExit(2)
    match = RE_CONTRACTS_VERSION.search(CONTRACTS.read_text())
    if not match:
        print("plan-check: 02_CONTRACTS.md has no '**Contract version: vX.Y**'", file=sys.stderr)
        raise SystemExit(2)
    return int(match.group(1)), int(match.group(2))


def plan_files() -> list[PlanFile]:
    """Every markdown file in the plan, with the version in its header (if any)."""
    out: list[PlanFile] = []
    for path in sorted(PLAN_DIR.rglob("*.md")):
        if "reports" in path.relative_to(PLAN_DIR).parts:
            continue  # reports record history; they are never "stale"
        if "templates" in path.relative_to(PLAN_DIR).parts:
            continue
        match = RE_HEADER.search(path.read_text())
        version = (int(match.group(1)), int(match.group(2))) if match else None
        out.append(PlanFile(path=path, version=version))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict-missing",
        action="store_true",
        help="also fail on phase files that carry no contracts header at all",
    )
    args = parser.parse_args()

    current = current_version()
    files = plan_files()
    stale = [f for f in files if f.version is not None and f.version < current]
    ahead = [f for f in files if f.version is not None and f.version > current]
    missing = [f for f in files if f.version is None and f.path.name not in NO_HEADER_EXPECTED]

    print(f"plan-check: contracts v{current[0]}.{current[1]} ({len(files)} plan files)")

    # `ahead` and `stale` are filtered on `version is not None`; spell it for the checker.
    for f in ahead:
        assert f.version is not None
        print(f"  AHEAD   {f.rel}: v{f.version[0]}.{f.version[1]} > 02_CONTRACTS.md")
    for f in stale:
        assert f.version is not None
        print(f"  STALE   {f.rel}: v{f.version[0]}.{f.version[1]}")
    for f in missing:
        print(f"  NOHDR   {f.rel}: no 'contracts: vX.Y' header")

    failed = bool(stale or ahead) or (args.strict_missing and bool(missing))
    if not failed:
        print(f"plan-check: ok — 0 stale, {len(missing)} without a header (allowed)")
        return 0
    print(f"plan-check: FAILED — {len(stale)} stale, {len(ahead)} ahead, {len(missing)} no-header")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
