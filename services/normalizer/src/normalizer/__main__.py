"""The normalizer service: `raw.*` in, `norm.<category>` + `lineage` + `dlq` out.

One Kafka transaction per batch, with the consumed offsets sent inside it
(`veyra_common.kafka.TxnProcessor`), so a crash cannot duplicate output or skip input — the
property CP1 check 7 and A3 task 9 both test by killing the process mid-stream.

    python -m normalizer

What it does per record: validate the envelope, normalize it, and emit
* the OCSF event to `norm.<category>` keyed by `event_uid`,
* an IF-LINEAGE row keyed by `event_uid`,
* an IF-DLQ copy when the tier is 2 or worse (P2: a copy, never a drop).

A record that is not a valid envelope still produces a DLQ record, because "nothing crashes on
bad input" is a quality-bar requirement, not an aspiration. A record that kills the *process* is
caught one level up: ``TxnProcessor`` journals the in-flight batch, and after
``VEYRA_POISON_MAX_RETRIES`` restarts on the same offsets the batch is skipped with a tier 4
``engine_crash`` DLQ record per message (:func:`poison_records`).
"""

from __future__ import annotations

import logging
import signal
from typing import Any

from confluent_kafka import Message
from prometheus_client import Counter, Gauge, Histogram

from normalizer.control import ControlFollower
from veyra_common.ids import monotonic_us
from veyra_common.kafka import OutputRecord, TxnProcessor
from veyra_common.models import DlqRecord, Envelope, LineageRecord, RawRef
from veyra_common.service import ServiceApp
from veyra_common.settings import ServiceSettings
from veyra_common.topics import (
    RAW_PATTERN,
    TOPIC_DLQ,
    TOPIC_LINEAGE,
    TOPIC_REPLAY_RAW,
    norm_topic,
)
from veyra_engine import Engine, EngineContext, serialize

log = logging.getLogger(__name__)

EVENTS = Counter("veyra_norm_events_total", "Normalized events", ["tier", "source"])
LATENCY = Histogram(
    "veyra_norm_latency_us",
    "Per-event normalization latency (microseconds)",
    buckets=(50, 100, 250, 500, 1000, 2500, 5000, 10000, 50000),
)
ENGINE_ERRORS = Counter("veyra_norm_engine_errors_total", "Engine failures", ["kind"])
CONTRACTS_LOADED = Gauge("veyra_control_contracts_loaded", "Contracts currently loaded")
BATCHES = Counter("veyra_norm_batches_total", "Transactions committed")


class NormalizerSettings(ServiceSettings):
    """Service section for the normalizer."""

    service_name: str = "normalizer"
    metrics_port: int = 8201
    consumer_group: str = "normalizer"
    instance: str = "0"


def build_outputs(engine: Engine, message: Message, cfg: NormalizerSettings) -> list[OutputRecord]:
    """Normalize one Kafka message into the records its transaction should produce."""
    raw_value = message.value()
    started = monotonic_us()

    try:
        envelope = Envelope.model_validate_json(raw_value)
    except Exception as exc:
        # Not a valid envelope: emit a DLQ record so the event is accounted for, and move on.
        ENGINE_ERRORS.labels("invalid_envelope").inc()
        log.error(
            "invalid envelope",
            extra={"topic": message.topic(), "offset": message.offset(), "error": str(exc)},
        )
        broken = DlqRecord(
            event_uid="00000000-0000-7000-8000-000000000000",
            tenant_id="unassigned",
            source_id="unregistered",
            tier=4,
            reason_code="schema_invalid",
            reason_detail=f"envelope did not validate: {exc}"[:2000],
            template_sig="t_000000000000",
            text_masked="",
            parse_path=["envelope_invalid"],
            produced_at="1970-01-01T00:00:00.000000000Z",
        )
        return [
            OutputRecord(
                topic=TOPIC_DLQ, key=broken.source_id, value=broken.model_dump_json().encode()
            )
        ]

    result = engine.normalize(envelope)
    LATENCY.observe(monotonic_us() - started)
    EVENTS.labels(str(result.tier), envelope.source_id).inc()

    # The engine cannot know where the message sat in Kafka; the service does, and IF-ULPF wants
    # the real coordinates so evidence can be walked back to the exact record.
    raw_ref = RawRef(topic=message.topic(), partition=message.partition(), offset=message.offset())
    result.ulpf["raw_ref"] = raw_ref.model_dump()
    result.ocsf["ulpf"] = result.ulpf

    replay_block = _replay_block(raw_value)
    if replay_block:
        # A5 owns replay semantics; the service already carries them through so a replayed event
        # is never mistaken for a first-time one.
        result.ulpf["replay"] = True
        result.ulpf["revision"] = int(replay_block.get("revision", 2))
        result.ulpf["supersedes"] = replay_block.get("supersedes")

    outputs = [
        OutputRecord(
            topic=norm_topic(result.category),
            key=envelope.event_uid,
            value=serialize(result.ocsf),
        ),
        OutputRecord(
            topic=TOPIC_LINEAGE,
            key=envelope.event_uid,
            value=_lineage(envelope, result, raw_ref, replay_block).model_dump_json().encode(),
        ),
    ]
    if result.dlq is not None:
        outputs.append(
            OutputRecord(
                topic=TOPIC_DLQ,
                key=envelope.source_id,
                value=result.dlq.model_dump_json().encode(),
            )
        )
    return outputs


def poison_records(batch: list[Message], exc: Exception) -> list[OutputRecord]:
    """DLQ copies for a batch that is being skipped because processing it keeps killing us.

    Skipping is the only way out of a crash loop, but P2 says nothing is ever dropped silently — so
    every message in the batch leaves a tier 4 ``engine_crash`` record naming its Kafka coordinates,
    which is enough to replay it by hand once the cause is fixed.
    """
    outputs: list[OutputRecord] = []
    for message in batch:
        ENGINE_ERRORS.labels("poison").inc()
        coordinates = f"{message.topic()}[{message.partition()}]@{message.offset()}"
        log.error("poison record skipped", extra={"record": coordinates, "error": str(exc)})
        record = DlqRecord(
            event_uid=_event_uid_of(message),
            tenant_id="unassigned",
            source_id="unregistered",
            tier=4,
            reason_code="engine_crash",
            reason_detail=f"skipped after repeated failures on {coordinates}: "
            f"{type(exc).__name__}: {exc}"[:2000],
            template_sig="t_000000000000",
            text_masked="",
            parse_path=["poison_skipped"],
            produced_at="1970-01-01T00:00:00.000000000Z",
        )
        outputs.append(
            OutputRecord(
                topic=TOPIC_DLQ, key=record.source_id, value=record.model_dump_json().encode()
            )
        )
    return outputs


def _event_uid_of(message: Message) -> str:
    """The record's own event_uid when it is readable, else the nil uuid.

    Parsing is exactly what may have killed us, so this is deliberately the cheapest possible look
    and it never raises.
    """
    import json

    try:
        payload = json.loads(message.value())
        uid = payload.get("event_uid")
    except Exception:
        return "00000000-0000-7000-8000-000000000000"
    return str(uid) if isinstance(uid, str) and uid else "00000000-0000-7000-8000-000000000000"


def _replay_block(raw_value: bytes) -> dict[str, Any] | None:
    """The optional ``replay`` block on ``replay.raw`` messages (IF-ENVELOPE + replay)."""
    import json

    try:
        payload = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return None
    block = payload.get("replay")
    return block if isinstance(block, dict) else None


def _lineage(
    envelope: Envelope,
    result: Any,
    raw_ref: RawRef,
    replay_block: dict[str, Any] | None,
) -> LineageRecord:
    contract = result.ulpf.get("contract")
    search_terms: list[str] = []
    for observable in result.ocsf.get("observables", []):
        value = str(observable.get("value", ""))
        if value and value not in search_terms:
            search_terms.append(value)
    for key in ("user.name", "src_endpoint.ip", "dst_endpoint.ip", "device.hostname"):
        value = _dig(result.ocsf, key)
        if value and str(value) not in search_terms:
            search_terms.append(str(value))

    return LineageRecord(
        event_uid=envelope.event_uid,
        revision=int(result.ulpf.get("revision", 1)),
        tenant_id=envelope.tenant_id,
        source_id=envelope.source_id,
        raw_ref=raw_ref,
        raw_sha256=envelope.raw_sha256,
        contract_ref=(f"{contract['id']}@{contract['version']}" if contract else None),
        template_sig=result.ulpf["template"]["sig"],
        template_id=result.ulpf["template"].get("id"),
        tier=result.tier,
        conformance=result.conformance,
        class_uid=int(result.ocsf.get("class_uid", 0)),
        category=result.category,  # type: ignore[arg-type]
        norm_topic=norm_topic(result.category),
        produced_at=envelope.received_time,
        replay=bool(result.ulpf.get("replay")),
        replay_job_id=(replay_block or {}).get("job_id"),
        search_terms=search_terms[:16],
    )


def _dig(event: dict[str, Any], path: str) -> Any:
    cursor: Any = event
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def main() -> None:
    cfg = NormalizerSettings()
    app = ServiceApp(name=cfg.service_name, metrics_port=cfg.metrics_port, cfg=cfg)

    engine = Engine(
        EngineContext(
            budget_us=cfg.engine_budget_us,
            peel_max_depth=cfg.peel_max_depth,
            max_event_bytes=cfg.max_event_bytes,
        )
    )

    follower = ControlFollower(
        engine,
        cfg=cfg,
        group=f"{cfg.consumer_group}-control-{cfg.instance}",
        on_change=lambda state: CONTRACTS_LOADED.set(len(state.contracts)),
    )
    follower.start()
    app.on_stop(follower.stop)

    # Readiness gate: do not consume raw events until the contract set is real, or everything
    # would be tier 4 for the first few seconds after every restart.
    if not follower.wait_ready(timeout=60):
        log.error("control topic did not settle in 60s; starting anyway with what we have")
    app.mark_ready()

    processor = TxnProcessor(
        name=cfg.service_name,
        group=cfg.consumer_group,
        topics=[TOPIC_REPLAY_RAW],
        pattern=RAW_PATTERN,
        fn=lambda batch: _process(engine, batch, cfg),
        instance=cfg.instance,
        cfg=cfg,
        on_poison=poison_records,
    )
    app.on_stop(processor.stop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: (processor.stop(), app.stop()))

    log.info("normalizer running", extra={"group": cfg.consumer_group})
    processor.run()


def _process(engine: Engine, batch: list[Message], cfg: NormalizerSettings) -> list[OutputRecord]:
    outputs: list[OutputRecord] = []
    for message in batch:
        outputs.extend(build_outputs(engine, message, cfg))
    BATCHES.inc()
    return outputs


if __name__ == "__main__":
    main()
