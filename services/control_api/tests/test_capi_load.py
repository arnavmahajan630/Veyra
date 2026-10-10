"""/load: the console's load test, a subprocess that has producer processes of its own."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from control_api import routes_load

# Stands in for tools/bench/load_raw.py: the same sample lines, and like it a parent whose
# producers are daemon multiprocessing children.
FAKE = """
import multiprocessing as mp, sys, time

def producer(beat):
    for _ in range(250):  # 5 s: long enough to outlive a stop, short enough not to be left behind
        with open(beat, "a") as f:
            f.write(".")
        time.sleep(0.02)

if __name__ == "__main__":
    mode, beat = sys.argv[1], sys.argv[2]
    events = int(sys.argv[sys.argv.index("--events") + 1])
    print(f"Pipeline load: {events:,} envelopes", flush=True)
    if mode == "fail":
        print("  Kafka is not answering; is the stack up and healthy?", flush=True)
        raise SystemExit(1)
    print(f"  {'elapsed':>8}  {'normalized':>12}  {'rate (ev/s)':>12}  {'backlog':>10}", flush=True)
    if mode == "produce":
        import signal
        mp.set_start_method("fork")
        p = mp.Process(target=producer, args=(beat,), daemon=True)
        p.start()
        def on_term(signum, frame):
            p.terminate()
            sys.exit(0)
        signal.signal(signal.SIGTERM, on_term)
        time.sleep(60)
    print(f"  {1:>7.0f}s  {events // 2:>12,}  {5000:>12,.0f}  {events // 2:>10,}", flush=True)
    print(f"  {2:>7.0f}s  {events + 7:>12,}  {5000:>12,.0f}  {0:>10,}", flush=True)
    print(f"  normalized {events + 7:,} events in 2 s -> average 5,000 ev/s", flush=True)
"""


@pytest.fixture
def load(client, login, tmp_path: Path, monkeypatch) -> Iterator[Callable[[str], Path]]:
    """Signed in, with the load command replaced by the stand-in in the given mode."""
    script, beat = tmp_path / "fake_load.py", tmp_path / "beat"
    script.write_text(FAKE)
    monkeypatch.setattr(routes_load, "state", routes_load.LoadState())
    login("admin@veyra")

    def _load(mode: str) -> Path:
        command = [sys.executable, str(script), mode, str(beat)]
        monkeypatch.setattr(routes_load, "LOAD_COMMAND", command)
        return beat

    yield _load
    client.post("/load/stop")


def wait_for(client, done: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while True:
        status = client.get("/load/status").json()
        if done(status) or time.monotonic() > deadline:
            return status
        time.sleep(0.02)


def beats(beat: Path) -> int:
    return beat.stat().st_size if beat.exists() else 0


def test_load_requires_a_session(client) -> None:
    assert client.get("/load/status").status_code == 401
    assert client.post("/load/start", json={"count": 10}).status_code == 401
    assert client.post("/load/stop").status_code == 401


def test_a_run_reports_progress_and_that_it_finished(client, load) -> None:
    load("ok")
    assert client.post("/load/start", json={"count": 1000}).status_code == 200
    status = wait_for(client, lambda s: not s["running"])
    # 1,007 were normalized (other traffic counts too); progress stops at the total.
    assert status["running"] is False
    assert status["sent"] == 1000
    assert status["total"] == 1000
    assert status["outcome"] == "finished"
    assert status["error"] is None


def test_a_failed_run_says_why(client, load) -> None:
    load("fail")
    client.post("/load/start", json={"count": 1000})
    status = wait_for(client, lambda s: not s["running"])
    assert status["outcome"] == "failed"
    assert "Kafka is not answering" in status["error"]


def test_a_missing_load_script_is_a_failed_run(client, load, monkeypatch) -> None:
    load("ok")
    monkeypatch.setattr(routes_load, "LOAD_COMMAND", ["/no/such/veyra-load-test"])
    client.post("/load/start", json={"count": 1000})
    status = wait_for(client, lambda s: not s["running"])
    assert status["outcome"] == "failed"
    assert "/no/such/veyra-load-test" in status["error"]


def test_stop_ends_the_producers_too_and_the_next_start_works(client, load) -> None:
    beat = load("produce")
    client.post("/load/start", json={"count": 1000})
    assert client.post("/load/start", json={"count": 1000}).status_code == 400
    wait_for(client, lambda _: beats(beat) > 0)
    assert beats(beat) > 0

    asked = time.monotonic()
    assert client.post("/load/stop").status_code == 200
    assert time.monotonic() - asked < 2
    status = client.get("/load/status").json()
    assert (status["running"], status["outcome"]) == (False, "stopped")

    time.sleep(0.1)
    produced = beats(beat)
    time.sleep(0.3)
    assert beats(beat) == produced  # nothing is still producing

    load("ok")
    assert client.post("/load/start", json={"count": 500}).status_code == 200
    assert wait_for(client, lambda s: not s["running"])["outcome"] == "finished"


def test_stop_with_nothing_running_and_a_count_of_zero(client, load) -> None:
    load("ok")
    assert client.post("/load/stop").json() == {"status": "stopped"}
    assert client.post("/load/start", json={"count": 0}).status_code == 422
    assert client.get("/load/status").json()["running"] is False
