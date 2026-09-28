"""lineage_indexer — Kafka to ClickHouse lineage index (B1).

    python -m lineage_indexer

Applies the ClickHouse migrations, then runs the indexer loop on the main thread.
/healthz and /metrics are on the metrics port (8205). SIGTERM stops the loop after a
final flush + commit.
"""

from __future__ import annotations

import logging
import time

from lineage_indexer.indexer import Indexer
from lineage_indexer.settings import IndexerSettings
from lineage_indexer.sink import ClickHouseSink
from veyra_common.service import ServiceApp
from veyra_lineage.client import make_client
from veyra_lineage.migrate import migrate

log = logging.getLogger("lineage_indexer")


def _migrate_when_ready(cfg: IndexerSettings) -> None:
    """ClickHouse may still be starting when compose starts us: retry, then give up."""
    deadline = time.monotonic() + cfg.index_ch_wait_s
    while True:
        try:
            applied = migrate(cfg, client=make_client(cfg, database=""))
            log.info("migrations checked", extra={"applied": applied})
            return
        except Exception as exc:
            if time.monotonic() >= deadline:
                raise
            log.warning("clickhouse not ready", extra={"error": str(exc)})
            time.sleep(2)


def main() -> None:
    cfg = IndexerSettings()
    app = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg)
    _migrate_when_ready(cfg)
    sink = ClickHouseSink(make_client(cfg), cfg.clickhouse_db, retries=cfg.index_insert_retries)
    indexer = Indexer.create(cfg, sink)
    app.on_stop(indexer.stop)
    app.mark_ready()
    try:
        indexer.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
