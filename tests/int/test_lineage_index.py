"""B1: Kafka -> ClickHouse lineage index, against the real broker and ClickHouse.

Marked ``int``: ``make test`` skips these, ``make test-int`` runs them. They cover the
acceptance criteria that only real infrastructure can prove:

* AC1 — ``raw_events`` count == envelopes sent, ``norm_lineage`` count == norm events
  per revision. Counts are reconciled against Kafka's own watermarks, so the test is
  honest about everything in the topics, not just what it published itself.
* AC2 — an insert that is never committed is re-consumed after a restart and does **not**
  produce duplicates (the replay guard plus ReplacingMergeTree).
* AC4 — ``search("103.21.4.77")`` finds the T3 events through ``search_terms``.

Each test uses its own ClickHouse database and its own consumer group, so a run never
disturbs the demo stack's own index.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from confluent_kafka import TopicPartition
from confluent_kafka.admin import AdminClient
from lineage_indexer.indexer import SUBSCRIPTION, Indexer
from lineage_indexer.settings import IndexerSettings
from lineage_indexer.sink import ClickHouseSink, dedup_token

from veyra_common.envelope import stamp
from veyra_common.topics import NORM_PREFIX, RAW_PREFIX, TOPIC_LINEAGE
from veyra_lineage import queries
from veyra_lineage.client import make_client
from veyra_lineage.migrate import migrate
from veyra_lineage.rows import RAW_EVENTS, KafkaPos, build_row

pytestmark = pytest.mark.int

REPO = Path(__file__).resolve().parents[2]
BOOTSTRAP = os.environ.get("VEYRA_KAFKA_BOOTSTRAP", "localhost:29092")
CH_URL = os.environ.get("VEYRA_CH_URL") or os.environ.get(
    "VEYRA_CLICKHOUSE_URL", "http://localhost:8123"
)
DEMO_IP = "103.21.4.77"  # the tier-3 fixture's source IP (fake_norm)
RAW_COUNT = 40
NORM_COUNT = 20


def _cfg(db: str, group: str) -> IndexerSettings:
    return IndexerSettings(
        _env_file=None,
        kafka_bootstrap=BOOTSTRAP,
        ch_url=CH_URL,
        clickhouse_db=db,
        index_group=group,
        index_batch_rows=500,
        index_batch_ms=200,
        metrics_port=0,
    )


def _publish(*args: str) -> None:
    """Run a demo publisher the way a developer would, so the test uses real records."""
    result = subprocess.run(
        [sys.executable, *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=180,
        env={**os.environ, "VEYRA_KAFKA_BOOTSTRAP": BOOTSTRAP},
    )
    assert result.returncode == 0, f"{args} failed: {result.stderr[-2000:]}"


def _topic_totals(prefixes: tuple[str, ...], exact: tuple[str, ...] = ()) -> dict[str, int]:
    """``{topic: message count}`` from Kafka's watermarks — the authority for AC1."""
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
    cluster = admin.list_topics(timeout=30)
    consumer_cfg = _cfg("unused", f"wm-{uuid.uuid4().hex[:6]}")
    from veyra_common.kafka import make_consumer

    consumer = make_consumer(consumer_cfg.index_group, cfg=consumer_cfg)
    totals: dict[str, int] = {}
    try:
        for name, meta in cluster.topics.items():
            if not (name.startswith(prefixes) or name in exact):
                continue
            count = 0
            for partition in meta.partitions:
                low, high = consumer.get_watermark_offsets(
                    TopicPartition(name, partition), timeout=30, cached=False
                )
                count += high - low
            totals[name] = count
    finally:
        consumer.close()
    return totals


def _drain(indexer: Indexer, *, idle_s: float = 6.0, timeout_s: float = 180.0) -> None:
    """Poll until nothing new arrives for ``idle_s``, then flush and commit."""
    deadline = time.monotonic() + timeout_s
    last_change = time.monotonic()
    seen = -1
    while time.monotonic() < deadline:
        indexer.poll_once()
        if indexer.records != seen:
            seen = indexer.records
            last_change = time.monotonic()
        elif time.monotonic() - last_change >= idle_s:
            break
    indexer.flush()


@pytest.fixture
def index() -> tuple[IndexerSettings, Indexer]:
    """A fresh database, a fresh consumer group, and an indexer wired to both."""
    suffix = uuid.uuid4().hex[:8]
    db = f"veyra_it_{suffix}"
    cfg = _cfg(db, f"lineage-indexer-it-{suffix}")
    admin = make_client(cfg, database="")
    migrate(cfg, client=admin, db=db)
    sink = ClickHouseSink(make_client(cfg), db)
    indexer = Indexer.create(cfg, sink)
    yield cfg, indexer
    try:
        indexer.consumer.close()
    finally:
        admin.command(f"DROP DATABASE IF EXISTS {db} SYNC")


def _counts(cfg: IndexerSettings) -> dict[str, int]:
    ch = make_client(cfg)
    tables = ("raw_events", "norm_events", "norm_lineage", "search_index")
    return {t: int(ch.command(f"SELECT count() FROM {cfg.clickhouse_db}.{t}")) for t in tables}


def test_subscription_matches_the_contract() -> None:
    """IF-TOPICS: the indexer consumes every topic the index is built from."""
    assert SUBSCRIPTION == (
        "^raw\\..*",
        "^norm\\..*",
        "lineage",
        "vault_index",
        "receipts",
        "dlq",
        "shadow",
        "audit",
    )


def test_migrations_are_idempotent(index: tuple[IndexerSettings, Indexer]) -> None:
    """Applying them twice changes nothing (the fixture already applied them once)."""
    cfg, _ = index
    admin = make_client(cfg, database="")
    assert migrate(cfg, client=admin, db=cfg.clickhouse_db) == []
    tables = {
        r[0]
        for r in admin.query(
            "SELECT name FROM system.tables WHERE database = {db:String}",
            parameters={"db": cfg.clickhouse_db},
        ).result_rows
    }
    assert {"raw_events", "norm_lineage", "mv_source_minute", "mv_route_minute"} <= tables


def test_counts_reconcile_with_kafka(index: tuple[IndexerSettings, Indexer]) -> None:
    """AC1: every envelope in ``raw.*`` becomes exactly one ``raw_events`` row, and every
    norm event exactly one ``norm_lineage`` row per revision."""
    cfg, indexer = index
    _publish("demo/tools/fake_raw.py", "--eps", "0", "--count", str(RAW_COUNT))
    _publish("demo/tools/fake_norm.py", "--eps", "0", "--count", str(NORM_COUNT))

    totals = _topic_totals((RAW_PREFIX, NORM_PREFIX), (TOPIC_LINEAGE,))
    raw_sent = sum(n for t, n in totals.items() if t.startswith(RAW_PREFIX))
    norm_sent = sum(n for t, n in totals.items() if t.startswith(NORM_PREFIX))
    lineage_sent = totals.get(TOPIC_LINEAGE, 0)
    assert raw_sent >= RAW_COUNT and norm_sent >= NORM_COUNT

    _drain(indexer)
    counts = _counts(cfg)
    assert counts["raw_events"] == raw_sent, "raw_events must equal envelopes sent"
    assert counts["norm_events"] == norm_sent
    assert counts["norm_lineage"] == lineage_sent, "one row per (event, revision)"
    # Offsets were committed, so a fresh indexer in the same group finds nothing left.
    assert indexer.commits > 0
    again = Indexer.create(cfg, indexer.sink)
    try:
        _drain(again, idle_s=3.0, timeout_s=40.0)
        assert again.records == 0, "committed offsets must not be re-read"
    finally:
        again.consumer.close()
    assert _counts(cfg) == counts


def test_restart_after_an_uncommitted_insert_does_not_duplicate(
    index: tuple[IndexerSettings, Indexer],
) -> None:
    """AC2: rows inserted without committing their offsets are re-consumed after a
    restart, and the replay guard keeps the index exact."""
    cfg, indexer = index
    _publish("demo/tools/fake_raw.py", "--eps", "0", "--count", str(RAW_COUNT))

    # Insert everything, then abandon the process before committing: flush() with every
    # partition in skip_commit is exactly what a crash between insert and commit leaves.
    deadline = time.monotonic() + 60
    while indexer.records == 0 and time.monotonic() < deadline:
        indexer.poll_once()
    _drain(indexer, idle_s=4.0, timeout_s=90.0)
    assert indexer.inserts > 0
    before = _counts(cfg)
    assert before["raw_events"] > 0

    uncommitted = {(t, p) for (t, p) in indexer._pending}
    indexer.flush(skip_commit=frozenset(uncommitted) or frozenset())
    indexer.consumer.close()

    # A second indexer in the same group re-reads those records.
    restarted = Indexer.create(cfg, indexer.sink)
    try:
        _drain(restarted, idle_s=4.0, timeout_s=90.0)
    finally:
        restarted.consumer.close()

    ch = make_client(cfg)
    after = _counts(cfg)
    assert after["raw_events"] == before["raw_events"], "a restart must not duplicate rows"
    distinct = int(ch.command(f"SELECT uniqExact(event_uid) FROM {cfg.clickhouse_db}.raw_events"))
    assert distinct == after["raw_events"], "every event_uid appears once"


def test_a_retried_insert_with_the_same_token_is_ignored(
    index: tuple[IndexerSettings, Indexer],
) -> None:
    """Deduplication, without needing a failure: the same batch and token, twice.

    This is what makes an insert retried after a timeout safe — ClickHouse may already
    have written the batch the client never got an answer for.
    """
    cfg, indexer = index
    now_ms = int(time.time() * 1000)
    rows = []
    ranges = {}
    for i in range(10):
        env = stamp(
            f"<134>Sep 26 14:05:11 fw01 app[233]: dedup probe {i} from {DEMO_IP}".encode(),
            collector_id="it",
            transport="syslog_udp",
            framing_method="datagram",
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
        )
        _, row = build_row(
            "raw.custom",
            env.model_dump_json().encode(),
            KafkaPos("raw.custom", 0, i, now_ms),
        )
        rows.append(row)
        ranges[("raw.custom", 0)] = (0, i)

    token = dedup_token(RAW_EVENTS.name, ranges)
    ch = make_client(cfg)
    count = f"SELECT count() FROM {cfg.clickhouse_db}.raw_events"

    indexer.sink.insert(RAW_EVENTS, rows, token)
    after_first = int(ch.command(count))
    assert after_first == len(rows)

    indexer.sink.insert(RAW_EVENTS, rows, token)
    assert int(ch.command(count)) == after_first, "the same token must not insert twice"

    # A different batch (different token) is still inserted, so dedup is not too eager.
    other = dedup_token(RAW_EVENTS.name, {("raw.custom", 0): (10, 19)})
    indexer.sink.insert(RAW_EVENTS, rows, other)
    assert int(ch.command(count)) == after_first * 2


def test_queries_answer_from_the_indexed_data(index: tuple[IndexerSettings, Indexer]) -> None:
    """AC4 plus a smoke test of the query library against real indexed rows."""
    cfg, indexer = index
    _publish("demo/tools/fake_norm.py", "--eps", "0", "--count", "30", "--tier", "3")
    _publish("demo/tools/fake_raw.py", "--eps", "0", "--count", "20")
    _drain(indexer)
    ch = make_client(cfg)

    found = queries.search(DEMO_IP, client=ch)
    assert found.matched_on == "search_terms"
    assert found.hits, f"search({DEMO_IP!r}) found nothing"
    assert all(hit.tier == 3 for hit in found.hits)

    detail = queries.event_detail(found.hits[0].event_uid, client=ch)
    assert detail is not None
    assert detail.revisions and detail.revisions[0].ocsf is not None
    assert detail.raw_ref is not None

    overview = queries.overview(client=ch)
    assert overview.totals_by_tier.total > 0
    assert overview.sources

    health = queries.source_health(client=ch)
    assert any(h.source_id == "src_authsrv_01" for h in health)

    sig = found.hits[0].template_sig
    assert sig
    assert queries.template_events(sig, client=ch)
