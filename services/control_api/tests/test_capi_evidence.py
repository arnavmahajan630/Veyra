"""The real backtest/replay collaborators (C2): evidence-api over HTTP, raw envelopes from
Kafka by raw_ref, and the lineage watcher that counts a replay job's records."""

from __future__ import annotations

import json

import httpx
from capi_helpers import T3_SIG, t3_events
from control_api.evidence import EventRef, HttpEventIndex, KafkaRawStore, KafkaReplayWatcher


# ---------------------------------------------------------------- evidence-api
def test_http_index_reads_template_event_rows() -> None:
    rows = [
        {"event_uid": "e1", "tenant_id": "t", "source_id": "s", "revision": 2, "tier": 4,
         "raw_ref": {"topic": "raw.custom", "partition": 1, "offset": 7},
         "raw_sha256": "0" * 64, "produced_at": "2026-09-26T14:05:11Z"},
    ]  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/templates/{T3_SIG}/events"
        assert request.url.params["limit"] == "10"
        return httpx.Response(200, json=rows)

    client = httpx.Client(base_url="http://evidence", transport=httpx.MockTransport(handler))
    refs = HttpEventIndex("http://evidence", 5, client=client).template_events(T3_SIG, limit=10)
    assert refs == [EventRef("e1", "s", 2, 4, "raw.custom", 1, 7, "2026-09-26T14:05:11Z")]


def test_http_index_resolves_bare_uids_through_event_detail() -> None:
    detail = {
        "event_uid": "e1",
        "raw_ref": {"topic": "raw.custom", "partition": 0, "offset": 3},
        "raw": {"source_id": "src_authsrv_01"},
        "revisions": [{"revision": 1, "tier": 4}, {"revision": 2, "tier": 1}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/templates/"):
            return httpx.Response(200, json=["e1"])
        return httpx.Response(200, json=detail)

    client = httpx.Client(base_url="http://evidence", transport=httpx.MockTransport(handler))
    [ref] = HttpEventIndex("http://evidence", 5, client=client).template_events(T3_SIG, limit=5)
    assert (ref.revision, ref.tier, ref.offset, ref.source_id) == (2, 1, 3, "src_authsrv_01")


class _Msg:
    def __init__(self, offset: int, value: bytes) -> None:
        self._offset, self._value = offset, value

    def error(self) -> None:
        return None

    def offset(self) -> int:
        return self._offset

    def value(self) -> bytes:
        return self._value


class _FakeConsumer:
    def __init__(self, records: dict[tuple[str, int, int], bytes]) -> None:
        self.records = records
        self.position: tuple[str, int, int] | None = None
        self.closed = False

    def assign(self, partitions) -> None:  # type: ignore[no-untyped-def]
        tp = partitions[0]
        self.position = (tp.topic, tp.partition, tp.offset)

    def poll(self, timeout: float) -> _Msg | None:
        assert self.position is not None
        value = self.records.get(self.position)
        return None if value is None else _Msg(self.position[2], value)

    def close(self) -> None:
        self.closed = True


def test_kafka_raw_store_reads_the_envelope_at_each_raw_ref() -> None:
    [(ref, envelope), (gone, _)] = t3_events(2)
    consumer = _FakeConsumer({("raw.custom", 0, ref.offset): envelope.model_dump_json().encode()})
    store = KafkaRawStore(lambda: consumer, timeout_s=1.0)
    assert store.envelopes([ref, gone]) == [envelope]  # the second is past retention
    assert consumer.closed


# ---------------------------------------------------------------- the lineage watcher
class _LineageMsg:
    def __init__(self, value: dict) -> None:
        self._value = json.dumps(value).encode()

    def error(self) -> None:
        return None

    def value(self) -> bytes:
        return self._value


class _Topic:
    def __init__(self) -> None:
        self.partitions = {0: None, 1: None}


class _Meta:
    def __init__(self) -> None:
        self.topics = {"lineage": _Topic()}


class _LineageConsumer:
    def __init__(self, records: list[dict]) -> None:
        self.records = [_LineageMsg(r) for r in records]
        self.assigned: list = []
        self.closed = False

    def list_topics(self, topic: str, timeout: float) -> _Meta:
        return _Meta()

    def offsets_for_times(self, partitions, timeout: float):  # type: ignore[no-untyped-def]
        return partitions

    def assign(self, partitions) -> None:  # type: ignore[no-untyped-def]
        self.assigned = partitions

    def poll(self, timeout: float) -> _LineageMsg | None:
        return self.records.pop(0) if self.records else None

    def close(self) -> None:
        self.closed = True


def test_the_watcher_counts_distinct_events_of_its_own_job() -> None:
    records: list[dict] = [
        {"event_uid": "e1", "replay_job_id": "rj_1"},
        {"event_uid": "e1", "replay_job_id": "rj_1"},  # a redelivery counts once
        {"event_uid": "e2", "replay_job_id": "rj_other"},
        {"event_uid": "e3", "replay_job_id": None},
        {"event_uid": "e4", "replay_job_id": "rj_1"},
    ]
    consumer = _LineageConsumer(records)
    progress: list[int] = []
    watcher = KafkaReplayWatcher(lambda: consumer, poll_s=0.01)
    count = watcher.watch("rj_1", since_ms=5, expected=2, timeout_s=5, on_progress=progress.append)
    assert (count, progress) == (2, [1, 2])
    assert [(tp.partition, tp.offset) for tp in consumer.assigned] == [(0, 5), (1, 5)]
    assert consumer.closed


def test_the_watcher_gives_up_at_the_timeout() -> None:
    watcher = KafkaReplayWatcher(lambda: _LineageConsumer([]), poll_s=0.01)
    assert watcher.watch("rj_1", since_ms=0, expected=3, timeout_s=0.05, on_progress=print) == 0
