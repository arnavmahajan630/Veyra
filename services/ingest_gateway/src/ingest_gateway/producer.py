"""Produce envelopes and only acknowledge what Kafka has actually accepted.

The rule A2 sets is the one that makes an HTTP ingest path trustworthy: **never 200 on a maybe**. So
every message of a request carries a delivery callback, the producer is flushed with a timeout, and
the response is 200 only if every callback reported success. Anything else — a broker error, a
timeout, a queue that never drained — is a 503 with no partial acknowledgement, and the client
retries.

The consequence is at-least-once delivery: a retry after a 503 that actually succeeded produces the
same bytes again under a **new** ``event_uid``, and nothing downstream deduplicates it. That is
stated in `API.md` rather than hidden, because the alternative (a client-supplied idempotency key)
is not part of the HEC protocol.
"""

from __future__ import annotations

import logging
from typing import Any

from veyra_common.kafka import make_producer
from veyra_common.models import Envelope
from veyra_common.settings import Settings
from veyra_common.topics import raw_topic

log = logging.getLogger(__name__)


class DeliveryFailed(RuntimeError):
    """At least one envelope of the request was not durably accepted."""


class RawProducer:
    """Publishes IF-ENVELOPE records to ``raw.<vendor>``."""

    def __init__(self, cfg: Settings, *, producer: Any = None, name: str = "gateway-0") -> None:
        self.cfg = cfg
        self.producer = producer if producer is not None else make_producer(cfg=cfg)

    def send_all(self, envelopes: list[Envelope]) -> int:
        """Produce every envelope and block until all of them are acknowledged.

        Returns the number of events delivered; raises :class:`DeliveryFailed` if any of them was
        not, so the caller can answer 503 without having to interpret librdkafka's error objects.
        """
        if not envelopes:
            return 0
        errors: list[str] = []

        def on_delivery(err: Any, _msg: Any) -> None:
            if err is not None:
                errors.append(str(err))

        for envelope in envelopes:
            try:
                self.producer.produce(
                    raw_topic(envelope.vendor),
                    key=envelope.kafka_key(),
                    value=envelope.model_dump_json().encode(),
                    on_delivery=on_delivery,
                )
            except BufferError as exc:
                # The local queue is full, which means the broker is not keeping up. Flushing what
                # is already queued and failing the request is honest; dropping would not be.
                self.producer.flush(self.cfg.gateway_ack_timeout_ms / 1000)
                raise DeliveryFailed(f"producer queue full: {exc}") from exc

        remaining = self.producer.flush(self.cfg.gateway_ack_timeout_ms / 1000)
        if remaining:
            raise DeliveryFailed(
                f"{remaining} of {len(envelopes)} events not acknowledged within "
                f"{self.cfg.gateway_ack_timeout_ms} ms"
            )
        if errors:
            raise DeliveryFailed("; ".join(sorted(set(errors))[:3]))
        return len(envelopes)

    def close(self) -> None:
        self.producer.flush(10)
