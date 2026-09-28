"""control-api entry point: python -m control_api (compose service `control-api`)."""

from __future__ import annotations

from typing import cast

import httpx
import uvicorn

from control_api.app import create_app
from control_api.context import build_context, first_boot
from control_api.evidence import HttpEventIndex, KafkaRawStore, KafkaReplayWatcher
from control_api.publisher import ProducerLike
from veyra_common.kafka import make_consumer, make_producer
from veyra_common.service import ServiceApp
from veyra_common.settings import settings


def reset_drift_worker() -> None:
    """Tell the drift worker to forget its groups (called by /internal/reset, C3)."""
    url = f"{settings.drift_worker_url}/reset"
    httpx.post(url, timeout=settings.evidence_timeout_s).raise_for_status()


def main() -> None:
    service = ServiceApp(
        name="control_api", metrics_port=settings.control_api_port, serve_http=False
    )
    # confluent_kafka.Producer's produce() is a structural superset of ProducerLike
    # (extra keyword params with defaults); the cast documents that compatibility.
    ctx = build_context(
        settings,
        cast(ProducerLike, make_producer()),
        index=HttpEventIndex(settings.evidence_api_url, settings.evidence_timeout_s),
        # assign()-only readers: the group ids are never used for commits
        raw=KafkaRawStore(lambda: make_consumer("control-api-raw"), settings.evidence_timeout_s),
        watcher=KafkaReplayWatcher(
            lambda: make_consumer("control-api-replay"), settings.replay_poll_ms / 1000
        ),
        drift_reset=reset_drift_worker,
    )
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
