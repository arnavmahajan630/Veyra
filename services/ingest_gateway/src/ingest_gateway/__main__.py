"""ingest_gateway — S0 scaffold (owner A).

Serves /healthz and /metrics on port 8088 so compose and the checkpoints can probe it.
The real implementation lands in the owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="ingest_gateway", metrics_port=8088)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
