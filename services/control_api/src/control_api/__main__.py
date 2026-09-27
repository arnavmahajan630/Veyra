"""control-api entry point: python -m control_api (compose service `control-api`)."""

from __future__ import annotations

from typing import cast

import uvicorn

from control_api.app import create_app
from control_api.context import build_context, first_boot
from control_api.publisher import ProducerLike
from veyra_common.kafka import make_producer
from veyra_common.service import ServiceApp
from veyra_common.settings import settings


def main() -> None:
    service = ServiceApp(
        name="control_api", metrics_port=settings.control_api_port, serve_http=False
    )
    # confluent_kafka.Producer's produce() is a structural superset of ProducerLike
    # (extra keyword params with defaults); the cast documents that compatibility.
    ctx = build_context(settings, cast(ProducerLike, make_producer()))
    first_boot(ctx)  # seed if empty, republish_all, rewrite the inventory: ready means complete
    app = create_app(ctx)
    service.attach_fastapi(app)
    service.mark_ready()
    try:
        uvicorn.run(app, host="0.0.0.0", port=settings.control_api_port, log_config=None)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
