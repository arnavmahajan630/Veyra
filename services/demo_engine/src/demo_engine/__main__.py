"""demo_engine — Demo scenario runner, reset, preflight, tamper (owner B).

    python -m demo_engine

Serves the FastAPI app on port 8300 (IF-PORTS) with /healthz, /metrics, /scenario,
/stage/{n}, /reset, /preflight, /tamper.
"""

from __future__ import annotations

import uvicorn

from demo_engine.app import create_app
from veyra_common.service import ServiceApp


def main() -> None:
    app = ServiceApp(name="demo_engine", metrics_port=8300, serve_http=False)
    api = create_app()
    app.attach_fastapi(api)
    app.mark_ready()
    uvicorn.run(api, host="0.0.0.0", port=8300, log_config=None)


if __name__ == "__main__":
    main()
