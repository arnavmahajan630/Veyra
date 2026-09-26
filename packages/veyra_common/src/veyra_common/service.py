"""``ServiceApp`` — the base every VEYRA service starts from.

It gives a service the four things the quality bar demands of all of them:

* structured JSON logs;
* ``/healthz`` and ``/metrics`` (Prometheus text) on its metrics port;
* a readiness gate, so a service that has not finished reading ``control`` reports
  ``503`` instead of silently dropping traffic;
* SIGTERM handling that runs the registered stop hooks (flush, commit, close).

Worker services (normalizer, router, archiver, …) use the built-in stdlib server::

    app = ServiceApp(name="router", metrics_port=8202)
    app.on_stop(processor.stop)
    app.mark_ready()
    app.run_forever()

API services (FastAPI) reuse the same registry and expose the endpoints on their own
port instead::

    app.attach_fastapi(fastapi_app)
"""

from __future__ import annotations

import contextlib
import logging
import signal
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from veyra_common.logging import setup_logging
from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)

_READY = Gauge("veyra_service_ready", "1 when the service is ready to serve", ["service"])
_UP = Gauge("veyra_service_up", "1 while the service process is running", ["service"])


class ServiceApp:
    """Lifecycle, health and metrics for one service."""

    def __init__(
        self,
        *,
        name: str,
        metrics_port: int,
        cfg: Settings | None = None,
        serve_http: bool = True,
    ) -> None:
        self.name = name
        self.cfg = cfg or settings
        self.metrics_port = metrics_port
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self._stop_hooks: list[Callable[[], Any]] = []
        self._httpd: ThreadingHTTPServer | None = None

        setup_logging(name, self.cfg.log_level)
        _UP.labels(name).set(1)
        _READY.labels(name).set(0)

        if serve_http:
            self._start_http()
        self._install_signals()
        log.info(
            "service starting", extra={"metrics_port": metrics_port, "profile": self.cfg.profile}
        )

    # ---------------------------------------------------------------- readiness
    def mark_ready(self) -> None:
        """Called once the service can actually do its job."""
        self.ready.set()
        _READY.labels(self.name).set(1)
        log.info("service ready")

    def mark_unready(self, reason: str = "") -> None:
        self.ready.clear()
        _READY.labels(self.name).set(0)
        log.warning("service not ready", extra={"reason": reason})

    def health(self) -> tuple[int, dict[str, Any]]:
        """``(status_code, body)`` for ``/healthz``."""
        ok = self.ready.is_set() and not self.stopping.is_set()
        return (200 if ok else 503), {
            "service": self.name,
            "ready": self.ready.is_set(),
            "stopping": self.stopping.is_set(),
            "profile": self.cfg.profile,
        }

    # ---------------------------------------------------------------- shutdown
    def on_stop(self, hook: Callable[[], Any]) -> None:
        """Register a shutdown hook. Hooks run in reverse registration order."""
        self._stop_hooks.append(hook)

    def _install_signals(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            # ValueError => not on the main thread (tests); signals are then the caller's job.
            with contextlib.suppress(ValueError):
                signal.signal(sig, lambda *_: self.stop())

    def stop(self) -> None:
        """Graceful stop: mark unready, run hooks, close the HTTP server."""
        if self.stopping.is_set():
            return
        self.stopping.set()
        self.mark_unready("shutting down")
        for hook in reversed(self._stop_hooks):
            try:
                hook()
            except Exception:
                log.exception("stop hook failed")
        if self._httpd:
            threading.Thread(target=self._httpd.shutdown, daemon=True).start()
        _UP.labels(self.name).set(0)
        log.info("service stopped")

    def run_forever(self) -> None:
        """Block until a signal arrives (for services whose work runs in threads)."""
        try:
            while not self.stopping.wait(0.5):
                pass
        finally:
            self.stop()

    # ---------------------------------------------------------------- http
    def _start_http(self) -> None:
        app = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                if self.path.startswith("/metrics"):
                    body = generate_latest()
                    self._send(200, body, CONTENT_TYPE_LATEST)
                elif self.path.startswith("/healthz"):
                    import json

                    code, payload = app.health()
                    self._send(code, json.dumps(payload).encode(), "application/json")
                else:
                    self._send(404, b'{"error":"not found"}', "application/json")

            def _send(self, code: int, body: bytes, content_type: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: Any) -> None:
                pass  # health probes would drown the real logs

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.metrics_port), Handler)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, name="health", daemon=True).start()

    def attach_fastapi(self, api: Any) -> None:
        """Add ``/healthz`` and ``/metrics`` to a FastAPI app (API services)."""
        from fastapi import Response

        @api.get("/healthz")
        def healthz() -> Response:  # pragma: no cover - exercised via HTTP in tests
            import json

            code, payload = self.health()
            return Response(json.dumps(payload), status_code=code, media_type="application/json")

        @api.get("/metrics")
        def metrics() -> Response:  # pragma: no cover
            return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
