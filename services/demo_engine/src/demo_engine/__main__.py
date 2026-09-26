"""demo_engine — S0 scaffold (owner B).

Serves /healthz and /metrics on port 8300 so compose and the checkpoints can probe it.
The real implementation lands in the owning phase; see docs/plan.
"""

from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="demo_engine", metrics_port=8300)
    app.mark_ready()
    app.run_forever()


if __name__ == "__main__":
    main()
