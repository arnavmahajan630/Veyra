"""The drift worker's HTTP surface on its metrics port (IF-API-DEMO: ``:8206 POST /flush``).

``/flush`` forces emission (B7 stage 4 fallback), ``/reset`` forgets every group and
cluster (control-api calls it from ``/internal/reset``), ``/groups`` shows what the worker
is tracking. ``ServiceApp.attach_fastapi`` adds ``/healthz`` and ``/metrics``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from drift_worker.worker import DriftWorker


def create_app(worker: DriftWorker) -> FastAPI:
    app = FastAPI(title="VEYRA drift-worker", version="0.1.0")

    @app.post("/flush")
    def flush() -> dict[str, int]:
        return {"emitted": worker.tick(force=True)}

    @app.post("/reset")
    def reset() -> dict[str, bool]:
        worker.reset()
        return {"ok": True}

    @app.get("/groups")
    def groups() -> list[dict[str, Any]]:
        return worker.snapshot()

    return app
