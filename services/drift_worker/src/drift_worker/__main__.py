"""drift_worker — S0 scaffold (owner C).

Serves /healthz and /metrics on the metrics port 8206. The Kafka work lands in the
owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="drift_worker", metrics_port=8206)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
