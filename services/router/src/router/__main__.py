"""router — S0 scaffold (owner A).

Serves /healthz and /metrics on the metrics port 8202. The Kafka work lands in the
owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="router", metrics_port=8202)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
