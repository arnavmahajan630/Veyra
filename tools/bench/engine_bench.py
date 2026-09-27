"""Single-core engine microbench (A3 task 10, AC5: >= 1500 EPS/core for tier 1).

Measures the pure library, with no Kafka and no containers, so the number is about parsing
rather than plumbing:

    uv run python tools/bench/engine_bench.py
    uv run python tools/bench/engine_bench.py --seconds 5 --json

It reports per-corpus EPS plus a stage breakdown, because the interesting question when it is
slow is *which* stage — A3's risk list calls out jsonschema validation specifically, so
validation is measured separately rather than guessed at.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope
from veyra_engine import Engine, EngineContext, mini_compile
from veyra_engine.validate import validate_event

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "demo" / "corpus"
# The contract registry is its own repository, checked out beside this one
# (VEYRA_CONTRACTS_REPO overrides; see 05_CHANGELOG 2026-09-28).
REGISTRY = Path(os.environ.get("VEYRA_CONTRACTS_REPO", REPO.parent / "contracts-repo"))
SEED = REGISTRY / "t_ntro_core"
TEST_CONTRACTS = REPO / "packages" / "veyra_engine" / "tests" / "contracts"
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# name -> (corpus file, contract, source_id, vendor)
WORKLOADS: dict[str, tuple[str, Path, str, str]] = {
    "sshd (tier 1)": ("linux_sshd.log", SEED / "linux_sshd.yaml", "src_lnx_core_07", "linux"),
    "cef (tier 1)": (
        "acme_ngfw_cef.log",
        SEED / "acme_ngfw_cef.yaml",
        "src_fw_dmz_01",
        "acme_ngfw",
    ),
    "authsrv json (tier 1)": (
        "authsrv_t1_ok.log",
        TEST_CONTRACTS / "authsrv.yaml",
        "src_authsrv_01",
        "custom",
    ),
    "unregistered (tier 4)": (
        "ot_historian.log",
        TEST_CONTRACTS / "authsrv.yaml",
        "src_nobody",
        "unregistered",
    ),
}


def envelopes_for(corpus_file: str, source_id: str, vendor: str) -> list[Envelope]:
    raws = [framed.raw for framed in split_lines((CORPUS / corpus_file).read_bytes())]
    return [
        stamp(
            raw,
            collector_id="bench",
            transport="syslog_udp",
            framing_method="datagram",
            source_id=source_id,
            vendor=vendor,
            tenant_id="t_ntro_core",
            zone="core",
            received_time=RECEIVED,
        )
        for raw in raws
    ]


def measure(engine: Engine, envelopes: list[Envelope], seconds: float) -> dict[str, Any]:
    """Run for ``seconds`` and report EPS plus per-event latency percentiles."""
    latencies: list[float] = []
    tiers: dict[int, int] = {}
    count = 0
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        for envelope in envelopes:
            start = time.perf_counter()
            result = engine.normalize(envelope)
            latencies.append((time.perf_counter() - start) * 1_000_000)
            tiers[result.tier] = tiers.get(result.tier, 0) + 1
            count += 1
            if time.perf_counter() >= deadline:
                break
    latencies.sort()
    elapsed = sum(latencies) / 1_000_000
    return {
        "events": count,
        "eps": round(count / elapsed, 1) if elapsed else 0.0,
        "p50_us": round(statistics.median(latencies), 1),
        "p95_us": round(latencies[int(len(latencies) * 0.95)], 1),
        "p99_us": round(latencies[int(len(latencies) * 0.99)], 1),
        "tiers": {str(k): v for k, v in sorted(tiers.items())},
    }


def measure_validation(engine: Engine, envelopes: list[Envelope]) -> float:
    """How much of an event's time is schema validation? (A3 risk: jsonschema is slow.)

    The validators are warmed first: fastjsonschema compiles a schema to Python on first use,
    and folding that one-time cost into a single pass made validation look ~50x worse than it is.
    """
    events = [engine.normalize(envelope).ocsf for envelope in envelopes]
    for event in events:  # warm-up: compile per class_uid before timing anything
        validate_event(event)
    rounds = 20
    start = time.perf_counter()
    for _ in range(rounds):
        for event in events:
            validate_event(event)
    return (time.perf_counter() - start) / (rounds * len(events)) * 1_000_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=3.0, help="per workload")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    results: dict[str, Any] = {}
    for name, (corpus_file, contract_path, source_id, vendor) in WORKLOADS.items():
        engine = Engine(EngineContext())
        engine.load([mini_compile(contract_path.read_text())])
        envelopes = envelopes_for(corpus_file, source_id, vendor)
        row = measure(engine, envelopes, args.seconds)
        row["validate_us"] = round(measure_validation(engine, envelopes), 1)
        results[name] = row

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    print(f"{'workload':<24} {'EPS':>9} {'p50 us':>8} {'p95 us':>8} {'validate us':>12}  tiers")
    for name, row in results.items():
        print(
            f"{name:<24} {row['eps']:>9.1f} {row['p50_us']:>8.1f} {row['p95_us']:>8.1f} "
            f"{row['validate_us']:>12.1f}  {row['tiers']}"
        )

    tier1 = [row["eps"] for name, row in results.items() if "tier 1" in name]
    worst = min(tier1) if tier1 else 0.0
    target = 1500
    print(
        f"\nAC5 target: >= {target} EPS/core on tier 1. Slowest tier-1 workload: {worst:.0f} EPS."
    )
    print("PASS" if worst >= target else "BELOW TARGET")
    return 0 if worst >= target else 1


if __name__ == "__main__":
    raise SystemExit(main())
