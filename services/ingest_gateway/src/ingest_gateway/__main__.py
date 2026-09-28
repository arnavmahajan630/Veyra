"""ingest-gateway entry point: `python -m ingest_gateway` (compose service `ingest-gateway`).

Readiness is the thing to get right here. The gateway cannot authenticate anyone until it has both
the pepper (C1 writes it) and the API keys from `control`, so it stays **not ready** until it has
them, and requests that arrive meanwhile get 503 rather than a 401 storm that would make a correctly
configured shipper give up.
"""

from __future__ import annotations

import logging
import signal

import uvicorn

from ingest_gateway.app import GatewayContext, create_app
from ingest_gateway.pepper import load_pepper
from ingest_gateway.producer import RawProducer
from ingest_gateway.registry import KeyRegistry
from ingest_gateway.settings import GatewaySettings
from veyra_common.service import ServiceApp

log = logging.getLogger(__name__)


def main() -> None:
    cfg = GatewaySettings()
    service = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, serve_http=False)

    pepper = load_pepper(cfg.keys_dir)
    registry = KeyRegistry(pepper, cfg=cfg)
    registry.start()
    service.on_stop(registry.stop)

    producer = RawProducer(cfg, name=f"gateway-{cfg.instance}")
    service.on_stop(producer.close)

    ctx = GatewayContext(cfg=cfg, registry=registry, producer=producer)
    app = create_app(ctx)
    service.attach_fastapi(app)

    if not registry.wait_ready(timeout=60):
        # Starting anyway is right: an empty control topic is a legitimate state on a fresh stack,
        # and every request will answer 503 until the keys arrive.
        log.error("control topic did not settle in 60s; starting without keys")
    else:
        log.info(
            "gateway registry ready",
            extra={"keys": registry.key_count, "sources": registry.source_count},
        )
    service.mark_ready()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: service.stop())

    log.info("ingest-gateway running", extra={"port": cfg.metrics_port})
    try:
        uvicorn.run(app, host="0.0.0.0", port=cfg.metrics_port, log_config=None)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
