"""The drift worker as a service (C3): /flush, /reset, /groups, and the DLQ consumer step."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from drift_worker.__main__ import step
from drift_worker.app import create_app
from drift_worker.worker import DriftWorker
from fastapi.testclient import TestClient
from test_drift_worker import record, t3_texts

from veyra_common.settings import Settings


@pytest.fixture
def posted() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def worker(tmp_path: Path, posted: list[dict[str, Any]]) -> DriftWorker:
    cfg = Settings(_env_file=None, data_dir=tmp_path / "data")  # type: ignore[call-arg]
    return DriftWorker(cfg, posted.append)


def test_the_http_surface(worker: DriftWorker, posted: list[dict[str, Any]]) -> None:
    worker.handle(record(0, t3_texts()[0]))
    client = TestClient(create_app(worker))
    assert client.get("/groups").json()[0]["count"] == 1
    assert client.post("/flush").json() == {"emitted": 1} and len(posted) == 1
    assert client.post("/reset").json() == {"ok": True}
    assert client.get("/groups").json() == []


class _Msg:
    def __init__(self, value: bytes) -> None:
        self._value = value

    def error(self) -> None:
        return None

    def value(self) -> bytes:
        return self._value


class _Consumer:
    def __init__(self, values: list[bytes]) -> None:
        self.values = values

    def poll(self, timeout: float) -> _Msg | None:
        return _Msg(self.values.pop(0)) if self.values else None


def test_step_reads_valid_records_and_skips_bad_ones(worker: DriftWorker) -> None:
    good = record(0, t3_texts()[0]).model_dump_json().encode()
    consumer = _Consumer([good, json.dumps({"not": "a dlq record"}).encode()])
    assert step(consumer, worker, 0.01) is True
    assert step(consumer, worker, 0.01) is True
    assert step(consumer, worker, 0.01) is False
    assert [p["count"] for p in worker.snapshot()] == [1]
