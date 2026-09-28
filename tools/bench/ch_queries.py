"""Benchmark every lineage query function (B1 AC3: p95 < 50 ms on 1M rows).

    python tools/seed_ch.py --db veyra_bench --reset          # 1M events
    python tools/bench/ch_queries.py --db veyra_bench          # measure, print a table
    python tools/bench/ch_queries.py --db veyra_bench --json out.json

Each case calls the real ``veyra_lineage.queries`` function (SQL + HTTP + Pydantic), the
same way the evidence API will, with inputs sampled from the dataset so the calls hit
different keys (no single warm row). Exits 1 if any case misses the p95 target.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from veyra_common.settings import settings
from veyra_lineage import queries
from veyra_lineage.client import make_client

TARGET_P95_MS = 50.0


def _pct(sorted_ms: list[float], p: float) -> float:
    idx = min(len(sorted_ms) - 1, max(0, round(p / 100 * len(sorted_ms)) - 1))
    return sorted_ms[idx]


def sample_inputs(ch: Any, n: int, rng: random.Random) -> dict[str, list[Any]]:
    def col(sql: str) -> list[Any]:
        return [r[0] for r in ch.query(sql, query_formats={"FixedString": "string"}).result_rows]

    uids = col(f"SELECT event_uid FROM norm_lineage ORDER BY cityHash64(event_uid) LIMIT {n}")
    shas = col(f"SELECT raw_sha256 FROM raw_events ORDER BY cityHash64(event_uid) LIMIT {n}")
    sig_src = ch.query(
        "SELECT template_sig, source_id FROM norm_lineage GROUP BY 1, 2 "
        f"ORDER BY cityHash64(template_sig) LIMIT {n}"
    ).result_rows
    dlq = ch.query(
        "SELECT source_id, template_sig FROM dlq_events GROUP BY 1, 2 "
        f"ORDER BY cityHash64(template_sig) LIMIT {n}"
    ).result_rows
    terms = col(
        "SELECT t FROM (SELECT arrayJoin(search_terms) AS t FROM norm_lineage LIMIT 200000) "
        f"GROUP BY t ORDER BY cityHash64(t) LIMIT {n}"
    )
    tenants = [*col("SELECT DISTINCT tenant_id FROM mv_source_minute"), None]
    contracts = col("SELECT DISTINCT contract_id FROM shadow_diffs") or ["authsrv"]
    rng.shuffle(uids)
    return {
        "uids": uids,
        "sha_prefixes": [s[: rng.choice((8, 12, 16, 64))] for s in shas],
        "sig_src": sig_src,
        "dlq": dlq,
        "terms": ["103.21.4.77", *terms],
        "tenants": tenants,
        "contracts": contracts,
    }


def cases(ch: Any, inp: dict[str, list[Any]]) -> dict[str, Callable[[int], Any]]:
    def pick(key: str, i: int) -> Any:
        values = inp[key]
        return values[i % len(values)]

    return {
        "overview": lambda i: queries.overview(pick("tenants", i), client=ch),
        "source_health": lambda i: queries.source_health(
            pick("tenants", i), {"src_authsrv_01": 5.0, "src_missing_99": 1.0}, client=ch
        ),
        "search[event_uid]": lambda i: queries.search(pick("uids", i), client=ch),
        "search[sha_prefix]": lambda i: queries.search(pick("sha_prefixes", i), client=ch),
        "search[search_terms]": lambda i: queries.search(pick("terms", i), limit=50, client=ch),
        "search[template_sig]": lambda i: queries.search(pick("sig_src", i)[0], client=ch),
        "event_detail": lambda i: queries.event_detail(pick("uids", i), client=ch),
        "template_events": lambda i: queries.template_events(
            pick("sig_src", i)[0], pick("sig_src", i)[1], 100, client=ch
        ),
        "dlq_samples": lambda i: queries.dlq_samples(
            pick("dlq", i)[0], pick("dlq", i)[1], 20, client=ch
        ),
        "shadow_summary": lambda i: queries.shadow_summary(
            pick("contracts", i), timedelta(hours=24), client=ch
        ),
        "route_stats": lambda i: queries.route_stats(timedelta(hours=1), client=ch),
    }


def run(db: str, iterations: int, warmup: int, seed: int) -> dict[str, dict[str, float]]:
    ch = make_client(database=db)
    rows = int(ch.query("SELECT count() FROM raw_events").result_rows[0][0])
    lineage_rows = int(ch.query("SELECT count() FROM norm_lineage").result_rows[0][0])
    print(f"dataset {db}: raw_events={rows:,} norm_lineage={lineage_rows:,}")
    rng = random.Random(seed)
    inp = sample_inputs(ch, max(iterations, 50), rng)
    results: dict[str, dict[str, float]] = {}
    for name, fn in cases(ch, inp).items():
        for i in range(warmup):
            fn(i)
        timings: list[float] = []
        for i in range(iterations):
            started = time.perf_counter()
            fn(i)
            timings.append((time.perf_counter() - started) * 1000)
        timings.sort()
        results[name] = {
            "n": float(len(timings)),
            "p50": statistics.median(timings),
            "p95": _pct(timings, 95),
            "p99": _pct(timings, 99),
            "max": timings[-1],
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--db", default=settings.clickhouse_db)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--json", dest="json_path", help="also write results here")
    args = parser.parse_args()

    results = run(args.db, args.iterations, args.warmup, args.seed)
    print(f"\n{'query':<22} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'max ms':>8}  target")
    failed = []
    for name, r in results.items():
        ok = r["p95"] < TARGET_P95_MS
        if not ok:
            failed.append(name)
        print(
            f"{name:<22} {r['p50']:8.1f} {r['p95']:8.1f} {r['p99']:8.1f} {r['max']:8.1f}  "
            f"{'PASS' if ok else 'FAIL'}"
        )
    if args.json_path:
        with open(args.json_path, "w") as fh:
            json.dump(
                {"db": args.db, "target_p95_ms": TARGET_P95_MS, "results": results}, fh, indent=2
            )
    print(f"\n{'all queries p95 < 50 ms' if not failed else 'missed target: ' + ', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
