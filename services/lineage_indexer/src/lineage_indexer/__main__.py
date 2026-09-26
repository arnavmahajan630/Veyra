"""lineage_indexer — S0 scaffold (owner B).

Serves /healthz and /metrics on the metrics port 8205. The Kafka work lands in the
owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="lineage_indexer", metrics_port=8205)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
