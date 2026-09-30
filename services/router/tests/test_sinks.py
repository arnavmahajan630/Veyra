"""Sinks: the file Wazuh follows, the socket a remote manager listens on, and the breaker.

The rotation test is the one that would be easy to get wrong and expensive to discover: Wazuh's
`<localfile>` holds the sink file open, so rotation has to keep the **same inode**. A rotation that
renamed the live file would leave the manager reading a file nobody writes to — no error anywhere,
alerts simply stop.
"""

from __future__ import annotations

import json
import socket
import threading
from pathlib import Path
from typing import Any

import pytest
from router.settings import RouterSettings
from router.sinks import (
    CLOSED,
    HALF_OPEN,
    OPEN,
    Breaker,
    HttpJsonSink,
    NdjsonFileSink,
    SinkError,
    SyslogTcpSink,
    build_sink,
)


# ---------------------------------------------------------------- ndjson_file
def test_one_json_object_per_line(tmp_path: Path) -> None:
    sink = NdjsonFileSink(tmp_path / "out.ndjson")
    sink.write([{"a": 1}, {"b": 2}])
    sink.flush()
    sink.close()
    lines = (tmp_path / "out.ndjson").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [{"a": 1}, {"b": 2}]


def test_it_creates_the_directory_and_appends_to_an_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "sinks" / "wazuh" / "veyra.ndjson"
    first = NdjsonFileSink(path)
    first.write([{"n": 1}])
    first.close()
    second = NdjsonFileSink(path)
    second.write([{"n": 2}])
    second.close()
    assert len(path.read_text().splitlines()) == 2, "a restart must not truncate the sink"


def test_rotation_keeps_the_live_inode(tmp_path: Path) -> None:
    """The whole reason rotation is written the way it is."""
    path = tmp_path / "out.ndjson"
    sink = NdjsonFileSink(path, rotate_bytes=200)
    sink.write([{"pad": "x" * 100}])
    inode_before = path.stat().st_ino
    sink.write([{"pad": "y" * 100}])  # crosses the threshold and rotates
    sink.write([{"after": True}])
    sink.close()

    assert path.stat().st_ino == inode_before, "Wazuh follows this inode; it must not change"
    assert path.with_suffix(".ndjson.1").exists(), "the old content is kept aside"
    remaining = [json.loads(line) for line in path.read_text().splitlines()]
    assert remaining == [{"after": True}], "the live file restarts empty after a rotation"


def test_a_failed_rotation_does_not_lose_the_event(tmp_path: Path, monkeypatch: Any) -> None:
    path = tmp_path / "out.ndjson"
    sink = NdjsonFileSink(path, rotate_bytes=50)
    monkeypatch.setattr(
        Path, "write_bytes", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full"))
    )
    sink.write([{"pad": "x" * 100}])  # triggers a rotation that cannot write the backup
    sink.close()
    assert json.loads(path.read_text().splitlines()[0])["pad"].startswith("x")


def test_the_file_sink_has_no_breaker(tmp_path: Path) -> None:
    assert NdjsonFileSink(tmp_path / "x.ndjson").breaker_state == CLOSED


# ---------------------------------------------------------------- syslog_tcp
class Listener:
    """A one-connection TCP server that records the framed bytes it receives."""

    def __init__(self) -> None:
        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.received = bytearray()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.socket.accept()
        except OSError:
            return
        with conn:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                self.received.extend(chunk)

    def close(self) -> None:
        self.socket.close()


def test_syslog_frames_are_octet_counted() -> None:
    """RFC 5425: a length prefix is the only framing that survives a JSON body with newlines."""
    listener = Listener()
    sink = SyslogTcpSink("127.0.0.1", listener.port)
    try:
        sink.write([{"message": "line one\nline two"}])
        deadline = threading.Event()
        deadline.wait(0.5)
        raw = bytes(listener.received)
    finally:
        sink.close()
        listener.close()

    length, _, body = raw.partition(b" ")
    assert int(length) == len(body), "the prefix must be the byte length of the frame"
    assert json.loads(body)["message"] == "line one\nline two"


def test_a_dead_collector_opens_the_breaker() -> None:
    """Port 1 is reliably refused, which is what a stopped remote manager looks like."""
    sink = SyslogTcpSink("127.0.0.1", 1, breaker_fails=2)
    for _ in range(2):
        with pytest.raises(SinkError):
            sink.write([{"a": 1}])
    assert sink.breaker_state == OPEN
    # Once open, a write fails immediately without another connection attempt.
    with pytest.raises(SinkError, match="breaker open"):
        sink.write([{"a": 1}])


def test_backoff_grows_while_the_far_end_is_down() -> None:
    sink = SyslogTcpSink("127.0.0.1", 1, breaker_fails=99)
    with pytest.raises(SinkError):
        sink.write([{"a": 1}])
    first = sink.retry_after
    with pytest.raises(SinkError):
        sink.write([{"a": 1}])
    assert sink.retry_after > first


# ---------------------------------------------------------------- breaker
def test_the_breaker_state_machine() -> None:
    breaker = Breaker(fails=3, cooldown_s=0.0)
    assert breaker.allow() and breaker.state == CLOSED
    for _ in range(3):
        breaker.failed()
    assert breaker.state == OPEN
    # With the cooldown elapsed, one probe is allowed through.
    assert breaker.allow()
    assert breaker.state == HALF_OPEN
    breaker.succeeded()
    assert breaker.state == CLOSED and breaker.failures == 0


def test_a_failed_probe_reopens_the_breaker() -> None:
    breaker = Breaker(fails=1, cooldown_s=0.0)
    breaker.failed()
    assert breaker.allow() and breaker.state == HALF_OPEN
    breaker.failed()
    assert breaker.state == OPEN


# ---------------------------------------------------------------- build_sink
def test_build_sink_reads_the_profile_defaults(cfg: RouterSettings, tmp_path: Path) -> None:
    sink = build_sink({"type": "ndjson_file", "path": str(tmp_path / "a.ndjson")}, cfg=cfg)
    assert isinstance(sink, NdjsonFileSink)
    assert sink.fsync_ms == cfg.route_fsync_ms
    assert sink.rotate_bytes == cfg.sink_rotate_bytes
    sink.close()


def test_syslog_falls_back_to_the_remote_host_setting(cfg: RouterSettings) -> None:
    """`WAZUH=remote` sets VEYRA_WAZUH_REMOTE_HOST; a route need not repeat it."""
    cfg.wazuh_remote_host = "siem.example.org"
    sink = build_sink({"type": "syslog_tcp"}, cfg=cfg)
    assert isinstance(sink, SyslogTcpSink)
    assert (sink.host, sink.port) == ("siem.example.org", cfg.wazuh_remote_port)


def test_a_syslog_route_with_no_host_anywhere_is_refused(cfg: RouterSettings) -> None:
    cfg.wazuh_remote_host = ""
    with pytest.raises(ValueError, match="needs a host"):
        build_sink({"type": "syslog_tcp"}, cfg=cfg)


def test_http_sink_needs_a_url(cfg: RouterSettings) -> None:
    with pytest.raises(ValueError, match="needs a url"):
        build_sink({"type": "http_json"}, cfg=cfg)
    assert isinstance(build_sink({"type": "http_json", "url": "http://x/y"}, cfg=cfg), HttpJsonSink)


def test_an_unknown_sink_type_is_refused(cfg: RouterSettings) -> None:
    with pytest.raises(ValueError, match="unknown sink type"):
        build_sink({"type": "smoke_signal"}, cfg=cfg)
