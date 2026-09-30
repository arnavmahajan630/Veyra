"""Pipeline load test: push N real envelopes into raw.* and measure what the normalizers sustain.

Stage B of ./veyra.sh load. Stage A (Kafka alone, up to 1B records) uses Kafka's own
kafka-producer-perf-test inside the broker container; this one exercises Veyra itself:
envelopes built from the demo corpus exactly like fake_raw.py, produced unpaced by N worker
processes, then consumed and normalized by every running normalizer instance.

    python tools/bench/load_raw.py --events 5000000 --workers 8
    python tools/bench/load_raw.py --events 200000 --measure-only   # watch an existing backlog

It prints the produce rate, then samples norm.* end offsets every --interval seconds until the
normalizers have caught up (or --timeout), and reports the sustained events/s and what 1B
events would take at that rate.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import sys
import time
from itertools import pairwise
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "demo" / "tools"))

from veyra_lib import bold, dim, green, topic_totals, yellow  # noqa: E402

# Corpus files whose sources are registered, so the normalizer does real tier-1 work.
FILES = ["linux_sshd.log", "acme_ngfw_cef.log"]


def worker(count: int, seed: int, done: mp.Queue) -> None:  # type: ignore[type-arg]
    import fake_raw  # the demo producer's corpus loader and envelope builder

    from veyra_common.kafka import make_producer
    from veyra_common.topics import raw_topic

    events = fake_raw.load(FILES)
    producer = make_producer(
        **{"linger.ms": 50, "batch.size": 1_000_000, "queue.buffering.max.messages": 500_000}
    )
    errors = 0

    def on_delivery(err: object, _msg: object) -> None:
        nonlocal errors
        if err is not None:
            errors += 1

    for i in range(count):
        profile, raw = events[(i + seed) % len(events)]
        envelope = fake_raw.build(profile, raw)
        while True:
            try:
                producer.produce(
                    raw_topic(envelope.vendor),
                    key=envelope.kafka_key(),
                    value=envelope.model_dump_json().encode(),
                    on_delivery=on_delivery,
                )
                break
            except BufferError:
                producer.poll(0.05)
        if i % 1000 == 0:
            producer.poll(0)
    producer.flush(120)
    done.put(errors)


def norm_total() -> int:
    return sum(topic_totals(("norm.",)).values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--events", type=int, default=1_000_000)
    parser.add_argument("--workers", type=int, default=max(2, min(16, (mp.cpu_count() or 4) // 2)))
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument(
        "--timeout", type=float, default=3600.0, help="stop measuring after this long"
    )
    parser.add_argument("--measure-only", action="store_true", help="do not produce; just measure")
    args = parser.parse_args(argv)

    print(bold(f"\nPipeline load: {args.events:,} envelopes, {args.workers} producer processes"))
    baseline = norm_total()
    target = baseline + args.events

    produce_started = time.monotonic()
    procs: list[mp.Process] = []
    done: mp.Queue = mp.Queue()  # type: ignore[type-arg]
    if not args.measure_only:
        share = args.events // args.workers
        for w in range(args.workers):
            n = share + (1 if w < args.events % args.workers else 0)
            p = mp.Process(target=worker, args=(n, w * 7919, done), daemon=True)
            p.start()
            procs.append(p)

    print(dim(f"  {'elapsed':>8}  {'normalized':>12}  {'rate (ev/s)':>12}  {'backlog':>10}"))
    samples: list[tuple[float, int]] = [(time.monotonic(), baseline)]
    produced_at: float | None = None
    while True:
        time.sleep(args.interval)
        now, total = time.monotonic(), norm_total()
        prev_t, prev_n = samples[-1]
        samples.append((now, total))
        if procs and produced_at is None and not any(p.is_alive() for p in procs):
            produced_at = now
            errors = sum(done.get() for _ in procs)
            took = produced_at - produce_started
            rate = args.events / took
            print(green(f"  produced {args.events:,} envelopes in {took:,.0f} s"))
            print(green(f"  ({rate:,.0f}/s, {errors} delivery errors)"))
        rate = (total - prev_n) / (now - prev_t)
        backlog = max(0, target - total)
        elapsed_s = now - produce_started
        print(f"  {elapsed_s:>7.0f}s  {total - baseline:>12,}  {rate:>12,.0f}  {backlog:>10,}")
        if total >= target or now - produce_started > args.timeout:
            break

    elapsed = samples[-1][0] - samples[0][0]
    done_n = samples[-1][1] - baseline
    moving = [((b[1] - a[1]) / (b[0] - a[0])) for a, b in pairwise(samples) if b[1] > a[1]]
    sustained = sorted(moving)[len(moving) // 2] if moving else 0.0
    peak = max(moving, default=0.0)
    average = done_n / elapsed if elapsed else 0.0
    print(bold("\n  Result"))
    print(f"  normalized {done_n:,} events in {elapsed:,.0f} s -> average {average:,.0f} ev/s")
    print(f"  sustained (median sample) {sustained:,.0f} ev/s, peak {peak:,.0f} ev/s")
    if sustained:
        hours = 1_000_000_000 / sustained / 3600
        print(
            yellow(f"  at this rate, 1B events through the full pipeline take about {hours:,.1f} h")
        )
    return 0 if done_n >= args.events or args.measure_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
