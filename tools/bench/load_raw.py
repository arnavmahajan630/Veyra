"""Pipeline load test: push N real envelopes into raw.* and measure what the normalizers sustain.

Stage B of ./veyra.sh load. Stage A (Kafka alone, up to 1B records) uses Kafka's own
kafka-producer-perf-test inside the broker container; this one exercises Veyra itself:
envelopes built from the demo corpus exactly like fake_raw.py, produced unpaced by N worker
processes, then consumed and normalized by every running normalizer instance.

    python tools/bench/load_raw.py --events 5000000 --workers 8
    python tools/bench/load_raw.py --events 200000 --measure-only   # watch an existing backlog

IF-TOPICS keys a record by its source, so one source fills one partition and only the normalizer
that owns it works. The corpus has two sources. To load every partition the events are sent as a
heavy hitter's are (IF-ENVELOPE): key ``source_id#n`` with ``salt=n``, over --salt-buckets values
of n. ``--salt-buckets 1`` sends them unsalted, which measures what one source can sustain.

It prints the produce rate, then samples the normalizers' committed offsets on raw.* every
--interval seconds until nothing is left queued (or --timeout, or --stall), and reports the
sustained events/s and what 1B events would take at that rate.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import queue
import sys
import time
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "demo" / "tools"))

from confluent_kafka import (  # noqa: E402
    OFFSET_BEGINNING,
    OFFSET_END,
    KafkaException,
    TopicPartition,
)
from veyra_lib import bold, dim, green, red, yellow  # noqa: E402

from veyra_common.kafka import make_consumer  # noqa: E402
from veyra_common.settings import settings  # noqa: E402

# Corpus files whose sources are registered, so the normalizer does real tier-1 work.
FILES = ["linux_sshd.log", "acme_ngfw_cef.log"]
GROUP = "normalizer"  # NormalizerSettings.consumer_group
# Keys per source for each partition of its topic. A key lands on a partition by hash, so it takes
# many more keys than partitions for every partition to get a near-equal share.
KEYS_PER_PARTITION = 64
# Starting the extra normalizers is itself a burst, so Kafka may be slow to answer the first ask.
START_TRIES = 12


def worker(count: int, seed: int, buckets: int, done: mp.Queue) -> None:  # type: ignore[type-arg]
    import fake_raw  # the demo producer's corpus loader and envelope builder

    from veyra_common.kafka import make_producer
    from veyra_common.topics import raw_topic

    events = fake_raw.load(FILES)
    producer = make_producer(
        **{"linger.ms": 50, "batch.size": 1_000_000, "queue.buffering.max.messages": 500_000}
    )
    errors = 0
    sent: Counter[str] = Counter()

    def on_delivery(err: object, _msg: object) -> None:
        nonlocal errors
        if err is not None:
            errors += 1

    for i in range(count):
        profile, raw = events[(i + seed) % len(events)]
        envelope = fake_raw.build(profile, raw)
        if buckets > 1:
            # Each source walks through every key in turn, so its partitions fill evenly.
            sent[envelope.source_id] += 1
            envelope.salt = sent[envelope.source_id] % buckets
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
    errors += producer.flush(120)  # whatever is still queued after that was not delivered either
    done.put((count, errors))


def _offsets(consumer: Any, partitions: list[TopicPartition], which: int) -> dict[Any, int]:
    """Every partition's first or last offset, in one request (a list-offsets by sentinel)."""
    asked = [TopicPartition(p.topic, p.partition, which) for p in partitions]
    return {(p.topic, p.partition): p.offset for p in consumer.offsets_for_times(asked, timeout=20)}


def progress() -> tuple[int, int] | None:
    """(raw events the normalizers have committed, raw events still queued), or None.

    Read from the normalizers' own offsets on raw.*, which move only when a transaction commits.
    The end offsets of norm.* look simpler and are wrong: they also count transaction markers and
    the records of aborted transactions, which once showed an empty backlog with 190,000 events
    still queued.

    None while Kafka cannot say. A broker that stalls under the burst leaves partitions without
    a leader for a few seconds; that is a sample to skip, not a reason to stop.
    """
    consumer = make_consumer(GROUP)  # never subscribes, so it reads the group without joining it
    try:
        partitions = [
            TopicPartition(topic, partition)
            for topic, meta in consumer.list_topics(timeout=20).topics.items()
            if topic.startswith("raw.")
            for partition in meta.partitions
        ]
        # One request each for every partition's start, end and committed offset. Asking partition
        # by partition took over a minute a sample on the workstation's 144 partitions.
        first = _offsets(consumer, partitions, OFFSET_BEGINNING)
        last = _offsets(consumer, partitions, OFFSET_END)
        committed = queued = 0
        for position in consumer.committed(partitions, timeout=20):
            key = (position.topic, position.partition)
            at = max(position.offset, first[key])  # never committed is a negative sentinel
            committed, queued = committed + at, queued + last[key] - at
        return committed, queued
    except KafkaException:
        return None
    finally:
        consumer.close()


def reports(done: mp.Queue, procs: list[mp.Process]) -> tuple[int, int, int]:  # type: ignore[type-arg]
    """(envelopes sent, deliveries that failed, producer processes that died without reporting)."""
    sent = failed = heard = 0
    for _ in procs:
        try:
            count, errors = done.get(timeout=5)
        except queue.Empty:
            break
        sent, failed, heard = sent + count, failed + errors, heard + 1
    return sent, failed, len(procs) - heard


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
    parser.add_argument(
        "--stall",
        type=float,
        default=300.0,
        help="give up when nothing has been normalized for this long with events still queued",
    )
    parser.add_argument(
        "--salt-buckets",
        type=int,
        default=KEYS_PER_PARTITION * settings.raw_partitions_per_vendor,
        help="keys each source is spread over (1 = unsalted: one partition per source)",
    )
    parser.add_argument("--measure-only", action="store_true", help="do not produce; just measure")
    args = parser.parse_args(argv)

    print(bold(f"\nPipeline load: {args.events:,} envelopes, {args.workers} producer processes"))
    start = progress()
    for _ in range(START_TRIES - 1):
        if start is not None:
            break
        print(dim("  Kafka did not answer; trying again"))
        time.sleep(args.interval)
        start = progress()
    if start is None:
        print(red("  Kafka is not answering; is the stack up and healthy?"))
        return 1
    baseline, backlog = start

    produce_started = time.monotonic()
    procs: list[mp.Process] = []
    done: mp.Queue = mp.Queue()  # type: ignore[type-arg]
    if not args.measure_only:
        share = args.events // args.workers
        for w in range(args.workers):
            n = share + (1 if w < args.events % args.workers else 0)
            job = (n, w * 7919, args.salt_buckets, done)
            p = mp.Process(target=worker, args=job, daemon=True)
            p.start()
            procs.append(p)

    print(dim(f"  {'elapsed':>8}  {'normalized':>12}  {'rate (ev/s)':>12}  {'backlog':>10}"))
    samples: list[tuple[float, int]] = [(time.monotonic(), baseline)]
    producing = bool(procs)
    # What this run put on raw.*, once known. Other traffic (the demo baseline) keeps arriving,
    # so the queue may never be seen empty; having committed this many is the finish line.
    delivered: int | None = None
    caught_up = False
    moved_at = samples[0][0]
    stalled = False
    while True:
        time.sleep(args.interval)
        now, sample = time.monotonic(), progress()
        prev_t, prev_n = samples[-1]
        if sample is None:
            print(dim("            Kafka did not answer this sample; trying again"))
        else:
            total, backlog = sample
            samples.append((now, total))
        if producing and not any(p.is_alive() for p in procs):
            producing = False
            sent, failed, died = reports(done, procs)
            took = now - produce_started
            print(green(f"  produced {sent:,} envelopes in {took:,.0f} s ({sent / took:,.0f}/s)"))
            if failed or died:
                print(yellow(f"  {failed:,} were not delivered; {died} producer process(es) died"))
            if not died:
                delivered = sent - failed
        if sample is not None:
            rate = (total - prev_n) / (now - prev_t)
            elapsed_s = now - produce_started
            print(f"  {elapsed_s:>7.0f}s  {total - baseline:>12,}  {rate:>12,.0f}  {backlog:>10,}")
            if total > prev_n:
                moved_at = now
            reached = delivered is not None and total - baseline >= delivered
            caught_up = not producing and (backlog == 0 or reached)
            if caught_up:
                break
        if now - produce_started > args.timeout:
            break
        if now - moved_at > args.stall:
            stalled = True
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
    if stalled:
        waited = f"{args.stall:,.0f} s"
        print(red(f"\n  nothing was normalized for {waited} with {backlog:,} still queued."))
        print(red("  The pipeline has stalled; the reason is in the normalizer and Kafka logs:"))
        print(red("    ./veyra.sh logs normalizer        docker logs --since 10m veyra-kafka"))
        return 1
    return 0 if caught_up or args.measure_only else 1


if __name__ == "__main__":
    raise SystemExit(main())
