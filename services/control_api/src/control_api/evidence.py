"""What control-api reads from the rest of the system for backtests and replays.

Three Protocols and their real implementations:

- ``EventIndex`` / ``HttpEventIndex``: which events carry a template sig, with each one's
  latest revision and ``raw_ref``. IF-API-EVIDENCE ``GET /lineage/templates/{sig}/events``;
  Caddy strips ``/api/lineage``, so evidence-api serves it at ``/templates/{sig}/events``,
  and that is what this client calls. IF-API-EVIDENCE says the endpoint returns event
  uids; B's ``TemplateEvent`` rows carry ``raw_ref`` too. Both shapes are accepted: a bare
  uid is resolved with ``GET /events/{uid}``.
- ``RawStore`` / ``KafkaRawStore``: the IF-ENVELOPE at a ``raw_ref``, read straight from
  ``raw.<vendor>`` (retention 3 days on the laptop, which covers every demo replay).
- ``ReplayWatcher`` / ``KafkaReplayWatcher``: counts the ``lineage`` records a replay job
  produced (``replay_job_id``), reading from the job's start time.

Reading ``raw.*`` and ``lineage`` makes control-api a consumer of both topics; decision
TC18 records it as an IF-TOPICS clarification.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from confluent_kafka import KafkaException, TopicPartition

from veyra_common.models import Envelope
from veyra_common.topics import TOPIC_LINEAGE


@dataclass(frozen=True)
class EventRef:
    event_uid: str
    source_id: str
    revision: int
    tier: int
    topic: str
    partition: int
    offset: int
    produced_at: str = ""


class EvidenceUnavailable(RuntimeError):
    """evidence-api or Kafka could not answer."""


class EventIndex(Protocol):
    def template_events(self, sig: str, *, limit: int) -> list[EventRef]: ...


class RawStore(Protocol):
    def envelopes(self, refs: Sequence[EventRef]) -> list[Envelope]: ...


class ReplayWatcher(Protocol):
    def watch(
        self,
        job_id: str,
        *,
        since_ms: int,
        expected: int,
        timeout_s: float,
        on_progress: Callable[[int], None],
    ) -> int: ...


# ---------------------------------------------------------------- evidence-api over HTTP
def _event_ref(item: dict[str, Any]) -> EventRef:
    raw_ref = item["raw_ref"]
    return EventRef(
        event_uid=str(item["event_uid"]),
        source_id=str(item.get("source_id", "")),
        revision=int(item.get("revision", 1)),
        tier=int(item.get("tier", 4)),
        topic=str(raw_ref["topic"]),
        partition=int(raw_ref["partition"]),
        offset=int(raw_ref["offset"]),
        produced_at=str(item.get("produced_at", "")),
    )


def _from_detail(detail: dict[str, Any]) -> dict[str, Any] | None:
    """B's ``EventDetail`` → the ``TemplateEvent`` shape (latest revision wins)."""
    revisions = detail.get("revisions") or []
    if not detail.get("raw_ref") or not revisions:
        return None
    latest = max(revisions, key=lambda r: int(r.get("revision", 1)))
    return {
        "event_uid": detail["event_uid"],
        "source_id": (detail.get("raw") or {}).get("source_id", ""),
        "revision": latest.get("revision", 1),
        "tier": latest.get("tier", 4),
        "raw_ref": detail["raw_ref"],
        "produced_at": latest.get("produced_at", ""),
    }


class HttpEventIndex:
    def __init__(self, base_url: str, timeout_s: float, client: httpx.Client | None = None):
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)

    def _get(self, path: str, **params: Any) -> Any:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EvidenceUnavailable(f"evidence-api {path}: {exc}") from exc
        return response.json()

    def template_events(self, sig: str, *, limit: int) -> list[EventRef]:
        refs: list[EventRef] = []
        for item in self._get(f"/templates/{sig}/events", limit=limit):
            row = _from_detail(self._get(f"/events/{item}")) if isinstance(item, str) else item
            if row is not None:
                refs.append(_event_ref(row))
        return refs[:limit]


# ---------------------------------------------------------------- Kafka
ConsumerFactory = Callable[[], Any]


class KafkaRawStore:
    """Reads single records by (topic, partition, offset); no group, no commits."""

    def __init__(self, consumer_factory: ConsumerFactory, timeout_s: float) -> None:
        self._factory = consumer_factory
        self._timeout_s = timeout_s

    def envelopes(self, refs: Sequence[EventRef]) -> list[Envelope]:
        if not refs:
            return []
        out: list[Envelope] = []
        consumer = self._factory()
        try:
            for ref in refs:
                consumer.assign([TopicPartition(ref.topic, ref.partition, ref.offset)])
                msg = consumer.poll(self._timeout_s)
                if msg is None or msg.error() or msg.offset() != ref.offset:
                    continue  # past retention or not there: reported as raw_missing
                out.append(Envelope.model_validate_json(msg.value()))
        except KafkaException as exc:
            raise EvidenceUnavailable(f"raw fetch: {exc}") from exc
        finally:
            consumer.close()
        return out


class KafkaReplayWatcher:
    """Counts distinct events a replay job re-normalized, as ``lineage`` records them."""

    def __init__(self, consumer_factory: ConsumerFactory, poll_s: float) -> None:
        self._factory = consumer_factory
        self._poll_s = poll_s

    def watch(
        self,
        job_id: str,
        *,
        since_ms: int,
        expected: int,
        timeout_s: float,
        on_progress: Callable[[int], None],
    ) -> int:
        consumer = self._factory()
        seen: set[str] = set()
        deadline = time.monotonic() + timeout_s
        try:
            meta = consumer.list_topics(TOPIC_LINEAGE, timeout=timeout_s)
            partitions = [
                TopicPartition(TOPIC_LINEAGE, p, since_ms)
                for p in sorted(meta.topics[TOPIC_LINEAGE].partitions)
            ]
            consumer.assign(consumer.offsets_for_times(partitions, timeout=timeout_s))
            while len(seen) < expected and time.monotonic() < deadline:
                msg = consumer.poll(self._poll_s)
                if msg is None or msg.error():
                    continue
                record = json.loads(msg.value())
                if record.get("replay_job_id") == job_id and record["event_uid"] not in seen:
                    seen.add(record["event_uid"])
                    on_progress(len(seen))
        except KafkaException as exc:
            raise EvidenceUnavailable(f"lineage watch: {exc}") from exc
        finally:
            consumer.close()
        return len(seen)
