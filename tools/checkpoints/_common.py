"""Shared harness for the CP1-CP4 checkpoint scripts.

The criteria themselves are the tables in ``docs/plan/shared/S1_integration_checkpoints.md``.

Checkpoint scripts only *observe*. They send input through the public paths — syslog sockets,
the HEC gateway, the REST APIs — and check the outputs in Kafka, ClickHouse, Wazuh's sink file
and the APIs. Nothing here reaches into a database or calls an internal endpoint, because a
check that uses a private path does not prove the public one works.

Every criterion prints one row: PASS, WARN or FAIL, its number from the S1 table, and what was
actually measured. A criterion that cannot be evaluated is a FAIL, never a pass — the same rule
the demo engine's expectations follow. WARN is reserved for the halves a human has to look at
and for capabilities this build declares as not implemented.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
TOOLS = REPO / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


@dataclass
class Row:
    status: str
    criterion: str
    detail: str = ""


Check = Callable[[], list[Row]]


def sh(*cmd: str, timeout: int = 120) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def verdict(ok: bool, criterion: str, detail: str = "") -> Row:
    return Row(PASS if ok else FAIL, criterion, detail)


def clickhouse_count(query: str, url: str) -> int | None:
    """One scalar out of ClickHouse over HTTP. None when it could not be asked."""
    import httpx

    try:
        response = httpx.post(url, content=query, timeout=15.0)
        response.raise_for_status()
        return int(response.text.strip() or 0)
    except Exception:
        return None


def wait_for(predicate: Callable[[], bool], timeout_s: float, interval_s: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def run(name: str, checks: dict[str, tuple[str, Check]], argv: Iterable[str] | None = None) -> int:
    """Run the selected criteria and print the table. Exit code is the number of failures."""
    parser = argparse.ArgumentParser(prog=f"tools/checkpoints/{name.lower()}.py")
    parser.add_argument(
        "--only",
        action="append",
        choices=sorted(checks),
        dest="only",
        help="run one criterion (repeatable)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    rows: list[Row] = []
    for key in args.only or sorted(checks):
        title, fn = checks[key]
        print(f"\n=== {key} {title} ===")
        try:
            produced = fn()
        except Exception as exc:  # a crashed check is a failed check, never a skipped one
            produced = [Row(FAIL, title, f"{type(exc).__name__}: {exc}")]
        width = max((len(row.criterion) for row in produced), default=10)
        for row in produced:
            print(f"{row.status:<5} {row.criterion:<{width}}  {row.detail}")
        rows += produced

    failures = sum(1 for row in rows if row.status == FAIL)
    warnings = sum(1 for row in rows if row.status == WARN)
    print(
        f"\n{name}: {len(rows) - failures - warnings} pass, {warnings} warn, {failures} fail"
        + ("" if failures else "  -> PASS")
    )
    return failures
