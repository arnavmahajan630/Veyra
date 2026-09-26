"""control_api — S0 scaffold (owner C).

Serves /healthz and /metrics on port 8000 so compose and the checkpoints can probe it.
The real implementation lands in the owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="control_api", metrics_port=8000)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
