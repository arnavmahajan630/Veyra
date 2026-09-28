"""drift-worker entry point: python -m drift_worker (compose service ``drift-worker``).

A consumer thread reads ``dlq`` (group ``drift-worker``) into the worker and ticks it; the
FastAPI app (``/flush``, ``/reset``, ``/groups``, ``/healthz``, ``/metrics``) runs on the
metrics port. Offsets are committed only after a checkpoint has saved the groups and the
Drain3 state, so a restart resumes where the saved state ends (C3 AC3).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx
import uvicorn
from confluent_kafka import KafkaError

from drift_worker.app import create_app
from drift_worker.worker import DriftWorker, Post
from veyra_common.kafka import make_consumer
from veyra_common.models import DlqRecord
from veyra_common.service import ServiceApp
from veyra_common.settings import Settings, settings
from veyra_common.topics import TOPIC_DLQ

log = logging.getLogger(__name__)


def http_post(cfg: Settings) -> Post:
    client = httpx.Client(base_url=cfg.control_api_url, timeout=cfg.evidence_timeout_s)

    def post(payload: dict[str, Any]) -> None:
        response = client.post("/internal/drift", json=payload)
        if response.status_code == 404:  # source deleted since: nothing to update, drop it
            log.warning("control-api does not know %s; dropping", payload["source_id"])
            return
        response.raise_for_status()

    return post


def step(consumer: Any, worker: DriftWorker, poll_s: float) -> bool:
    """Read at most one record into the worker, then tick. True if a record was read."""
    msg = consumer.poll(poll_s)
    read = False
    if msg is not None:
        if msg.error() is None:
            try:
                worker.handle(DlqRecord.model_validate_json(msg.value()))
            except ValueError:
                log.warning("skipping a DLQ record that does not validate")
            read = True
        elif msg.error().code() != KafkaError._PARTITION_EOF:
            log.error("dlq consumer error", extra={"error": str(msg.error())})
    worker.tick()
    return read


def consume(worker: DriftWorker, cfg: Settings, stopping: threading.Event) -> None:
    consumer = make_consumer("drift-worker", [TOPIC_DLQ], cfg=cfg)
    pending = False
    last = time.monotonic()
    try:
        while not stopping.is_set():
            pending |= step(consumer, worker, cfg.drift_poll_ms / 1000)
            if pending and (time.monotonic() - last) * 1000 >= cfg.drift_checkpoint_ms:
                worker.checkpoint()
                consumer.commit(asynchronous=False)
                pending, last = False, time.monotonic()
    finally:
        if pending:
            worker.checkpoint()
            consumer.commit(asynchronous=False)
        consumer.close()


def main() -> None:
    service = ServiceApp(
        name="drift_worker", metrics_port=settings.drift_worker_port, serve_http=False
    )
    worker = DriftWorker(settings, http_post(settings))
    app = create_app(worker)
    service.attach_fastapi(app)
    thread = threading.Thread(
        target=consume, args=(worker, settings, service.stopping), name="dlq", daemon=True
    )
    thread.start()
    service.on_stop(lambda: thread.join(timeout=10))
    service.mark_ready()
    try:
        uvicorn.run(app, host="0.0.0.0", port=settings.drift_worker_port, log_config=None)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
