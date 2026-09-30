"""End-to-end throughput bench: how many events per second does the pipeline sustain? (A6 task 8)

    uv run python tools/bench/throughput.py --count 20000
    uv run python tools/bench/throughput.py --count 20000 --replicas 1,2,4
    uv run python tools/bench/throughput.py --count 5000 --router
    uv run python tools/bench/throughput.py --report      # reports/A6-bench-<host>.md

Unlike `engine_bench.py`, which measures the library on one core, this measures the whole path —
Kafka, the envelope round trip, validation, the sink — because that is the number a slide can
honestly claim.

**Why worker processes rather than container replicas.** The plan asks for a curve at R in
{1, 2, 4}. Compose pins `container_name` on every VEYRA service (every Makefile target, health
check and integration test finds them by name), so `--scale` is not available without breaking
those. Instead the bench runs R **host processes** in one consumer group against a pre-loaded
`raw.bench` topic: same library, same broker, same group semantics, and the partition count bounds
parallelism either way. The report says which was measured.

The mix is 60 % tier 1 / 30 % tier 3 / 10 % tier 4, roughly what the demo corpus looks like and
deliberately not all tier 1 — a bench that only measures the fast path flatters itself.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import platform
import statistics
import time
from pathlib import Path
from typing import Any

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.models import Envelope
from veyra_common.settings import Settings, contracts_repo_path
from veyra_contracts import compile as compile_contract
from veyra_engine import Engine, EngineContext

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "demo" / "corpus"
SEED = contracts_repo_path() / "t_ntro_core"
TEST_CONTRACTS = REPO / "packages" / "veyra_engine" / "tests" / "contracts"
REPORTS = REPO / "docs" / "plan" / "reports"
BENCH_TOPIC = "raw.bench"
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# The mix, as (corpus file, source_id, vendor, share). sshd matches the seeded contract (tier 1),
# the historian matches nothing (tier 3), and the garbage blob is unparseable (tier 4).
MIX: tuple[tuple[str, str, str, float], ...] = (
    ("linux_sshd.log", "src_lnx_core_07", "linux", 0.60),
    ("ot_historian.log", "src_nobody", "unregistered", 0.30),
    ("garbage.bin", "src_nobody", "unregistered", 0.10),
)


def settings(bootstrap: str | None = None) -> Settings:
    if bootstrap:
        return Settings(_env_file=None, kafka_bootstrap=bootstrap)
    return Settings(_env_file=None, kafka_bootstrap="localhost:29092")


# ---------------------------------------------------------------- preload
def corpus_events() -> list[tuple[bytes, str, str]]:
    """``(raw, source_id, vendor)`` in the configured proportions, cycled to fill a run."""
    pools: list[tuple[list[bytes], str, str, float]] = []
    for name, source, vendor, share in MIX:
        blob = (CORPUS / name).read_bytes()
        if name.endswith(".bin"):
            chunks = [chunk for chunk in blob.split(b"\n") if chunk][:200]
        else:
            chunks = [event.raw for event in split_lines(blob)]
        if chunks:
            pools.append((chunks, source, vendor, share))
    out: list[tuple[bytes, str, str]] = []
    for chunks, source, vendor, share in pools:
        wanted = max(1, round(share * 100))
        for index in range(wanted):
            out.append((chunks[index % len(chunks)], source, vendor))
    return out


def preload(count: int, *, bootstrap: str | None = None) -> int:
    """Stamp and publish ``count`` envelopes to `raw.bench`. Returns what was accepted."""
    cfg = settings(bootstrap)
    producer = make_producer(cfg=cfg)
    template = corpus_events()
    published = 0
    for index in range(count):
        raw, source, vendor = template[index % len(template)]
        envelope = stamp(
            raw,
            collector_id="bench",
            transport="syslog_udp",
            framing_method="datagram",
            source_id=source,
            tenant_id="t_ntro_core",
            vendor=vendor,
            zone="core",
            received_time=RECEIVED,
        )
        producer.produce(
            BENCH_TOPIC, key=envelope.source_id, value=envelope.model_dump_json().encode()
        )
        published += 1
        if published % 5000 == 0:
            producer.poll(0)
    remaining = producer.flush(120)
    if remaining:
        raise SystemExit(f"{remaining} envelope(s) unacknowledged — is Kafka healthy?")
    return published


# ---------------------------------------------------------------- the worker
def _engine() -> Engine:
    engine = Engine(EngineContext())
    contracts = []
    candidates = (
        SEED / "linux_sshd.yaml",
        SEED / "acme_ngfw_cef.yaml",
        TEST_CONTRACTS / "authsrv.yaml",
    )
    for path in candidates:
        if path.exists():
            contracts.append(compile_contract(path.read_text()).model_dump())
    engine.load(contracts)
    return engine


def worker(group: str, target: int, out: Any, bootstrap: str | None = None) -> None:
    """Normalize until ``target`` events are consumed here, or the topic goes idle."""
    cfg = settings(bootstrap)
    engine = _engine()
    consumer = make_consumer(group, [BENCH_TOPIC], cfg=cfg, auto_offset_reset="earliest")
    tiers: dict[int, int] = {}
    processed = 0
    idle_until = time.monotonic() + 30
    started = time.monotonic()
    try:
        while processed < target and time.monotonic() < idle_until:
            message = consumer.poll(0.5)
            if message is None or message.error():
                continue
            idle_until = time.monotonic() + 10
            try:
                envelope = Envelope.model_validate_json(message.value())
            except Exception:
                continue
            result = engine.normalize(envelope)
            tiers[result.tier] = tiers.get(result.tier, 0) + 1
            processed += 1
    finally:
        consumer.close()
    out.put({"processed": processed, "seconds": time.monotonic() - started, "tiers": tiers})


def run_replicas(replicas: int, count: int, *, bootstrap: str | None = None) -> dict[str, Any]:
    """Consume `raw.bench` with ``replicas`` processes in one group; report sustained EPS."""
    group = f"bench-{replicas}-{int(time.time())}"
    queue: Any = mp.Queue()
    share = max(1, count // replicas)
    processes = [
        mp.Process(target=worker, args=(group, share, queue, bootstrap)) for _ in range(replicas)
    ]
    started = time.monotonic()
    for process in processes:
        process.start()
    results = [queue.get() for _ in processes]
    for process in processes:
        process.join(timeout=60)
    elapsed = time.monotonic() - started

    processed = sum(result["processed"] for result in results)
    tiers: dict[int, int] = {}
    for result in results:
        for tier, n in result["tiers"].items():
            tiers[int(tier)] = tiers.get(int(tier), 0) + n
    return {
        "replicas": replicas,
        "processed": processed,
        "seconds": round(elapsed, 2),
        "eps": round(processed / elapsed, 1) if elapsed else 0.0,
        "per_worker_eps": [
            round(result["processed"] / result["seconds"], 1) if result["seconds"] else 0.0
            for result in results
        ],
        "tiers": dict(sorted(tiers.items())),
    }


# ---------------------------------------------------------------- the router leg
def router_pass(count: int, *, bootstrap: str | None = None) -> dict[str, Any]:
    """How fast the router drains `norm.*` into the NDJSON sink, measured from the sink itself."""
    sink = REPO / "data" / "sinks" / "wazuh" / "veyra.ndjson"
    before = sink.stat().st_size if sink.exists() else 0
    cfg = settings(bootstrap)
    engine = _engine()
    producer = make_producer(cfg=cfg)
    template = corpus_events()

    from veyra_common.topics import norm_topic

    started = time.monotonic()
    for index in range(count):
        raw, source, vendor = template[index % len(template)]
        envelope = stamp(
            raw,
            collector_id="bench",
            transport="syslog_udp",
            framing_method="datagram",
            source_id=source,
            tenant_id="t_ntro_core",
            vendor=vendor,
            zone="core",
            received_time=RECEIVED,
        )
        result = engine.normalize(envelope)
        result.ocsf["ulpf"] = result.ulpf
        producer.produce(
            norm_topic(result.category),
            key=envelope.event_uid,
            value=json.dumps(result.ocsf).encode(),
        )
    producer.flush(120)
    published_at = time.monotonic()

    # Wait for the sink to stop growing, then measure how long the router took to catch up.
    last_size = -1
    size = sink.stat().st_size if sink.exists() else 0
    while size != last_size:
        time.sleep(2)
        last_size = size
        size = sink.stat().st_size if sink.exists() else 0
    drained_at = time.monotonic()
    return {
        "published": count,
        "publish_seconds": round(published_at - started, 2),
        "drain_seconds": round(drained_at - published_at, 2),
        "sink_bytes_written": size - before,
        "eps": round(count / (drained_at - started), 1),
    }


# ---------------------------------------------------------------- report
def machine() -> dict[str, str]:
    info = {
        "host": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpus": str(mp.cpu_count()),
    }
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()[0].split()
        info["memory"] = f"{int(meminfo[1]) // 1024 // 1024} GiB"
    except (OSError, IndexError, ValueError):
        pass
    return info


def write_report(results: list[dict[str, Any]], router: dict[str, Any] | None) -> Path:
    spec = machine()
    path = REPORTS / f"A6-bench-{spec['host'].split('.')[0]}.md"
    lines = [
        f"# A6 bench — {spec['host']}",
        "",
        "Measured end to end: Kafka in, normalized events out, on the machine below. Not a",
        "library microbench — for that see `tools/bench/engine_bench.py`.",
        "",
        "| Machine | |",
        "|---|---|",
        *(f"| {key} | {value} |" for key, value in spec.items()),
        "",
        "## Normalizer, host worker processes in one consumer group",
        "",
        "Compose pins `container_name`, so a container scaling curve is not available without",
        "breaking every Makefile target and test that finds services by name. These are host",
        "processes running the same library against the same broker; `raw.bench` has the profile's",
        "partition count, which is what bounds parallelism either way.",
        "",
        "| Replicas | Events | Seconds | EPS | Per-worker EPS | Tiers |",
        "|---|---|---|---|---|---|",
    ]
    for result in results:
        per_worker = ", ".join(str(value) for value in result["per_worker_eps"])
        lines.append(
            f"| {result['replicas']} | {result['processed']} | {result['seconds']} | "
            f"**{result['eps']}** | {per_worker} | {result['tiers']} |"
        )
    if router is not None:
        lines += [
            "",
            "## Router, NDJSON sink",
            "",
            f"- events published to `norm.*`: **{router['published']}**",
            f"- publish: {router['publish_seconds']} s",
            f"- router drain after the last publish: {router['drain_seconds']} s",
            f"- sink bytes written: {router['sink_bytes_written']}",
            f"- sustained: **{router['eps']} EPS**",
        ]
    lines += [
        "",
        "## Conditions",
        "",
        "- The full stack was up (Kafka, ClickHouse, Wazuh, the A services); these are not",
        "  numbers from an otherwise idle machine, which is the honest case for a demo laptop.",
        "- This laptop is thermally noisy — repeat runs vary, so treat these as the order of",
        "  magnitude, not a benchmark score.",
        "- The gateway's ceiling is dominated by events **per request**, since durability is paid",
        "  per request (A2): a bench of one event per POST measures HTTP, not VEYRA.",
        "- A canary in shadow mode doubles the engine work for that source (A5). Run one pass with",
        "  one attached and watch `veyra_shadow_skipped_total` before quoting a number for a",
        "  source under review.",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=20000, help="events per replica pass")
    parser.add_argument("--replicas", default="1,2,4", help="comma-separated worker counts")
    parser.add_argument("--router", action="store_true", help="also measure the router leg")
    parser.add_argument("--report", action="store_true", help="write the report file")
    parser.add_argument("--bootstrap", help="Kafka bootstrap (default localhost:29092)")
    parser.add_argument("--skip-preload", action="store_true", help="reuse what is on raw.bench")
    args = parser.parse_args()

    replicas = [int(value) for value in args.replicas.split(",") if value.strip()]
    results: list[dict[str, Any]] = []
    for count in replicas:
        if not args.skip_preload:
            published = preload(args.count, bootstrap=args.bootstrap)
            print(f"preloaded {published} envelopes to {BENCH_TOPIC}")
        result = run_replicas(count, args.count, bootstrap=args.bootstrap)
        print(
            f"replicas={result['replicas']:<2} {result['processed']:>6} events "
            f"in {result['seconds']:>6}s -> {result['eps']:>8} EPS  tiers={result['tiers']}"
        )
        results.append(result)

    router = router_pass(min(args.count, 5000), bootstrap=args.bootstrap) if args.router else None
    if router is not None:
        print(f"router: {router['eps']} EPS sustained ({router['drain_seconds']}s drain)")

    if results:
        best = max(result["eps"] for result in results)
        median = statistics.median(results[0]["per_worker_eps"])
        print(f"\npeak {best} EPS; median per-worker {median}")
    if args.report:
        path = write_report(results, router)
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
