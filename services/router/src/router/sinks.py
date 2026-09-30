"""Where a route's events actually go (IF-ROUTES ``sink``).

Three sinks, and each one's hard part is different:

* **`ndjson_file`** — Wazuh follows a *filename* (`<localfile>`), so rotation keeps the live
  name and copies the old content aside. The standard pattern (rename the live file to `.1`,
  open a new one) is wrong here: the manager's handle follows the inode, so it would keep
  reading the rotated file and see nothing new — silently — until someone restarted it.
* **`syslog_tcp`** — RFC 5425 octet-counting (`<length> <message>`), because a remote manager cannot
  frame a JSON document containing newlines any other way. Plus a breaker, so a dead collector costs
  one connection attempt per probe instead of one per event.
* **`http_json`** — a POST per batch, with the same breaker.

Every sink is used from exactly one route worker thread, so none of them needs its own lock.
"""

from __future__ import annotations

import json
import logging
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

# Breaker states, exported as `veyra_route_breaker_state` (0 closed, 1 open, 2 half-open).
CLOSED, OPEN, HALF_OPEN = 0, 1, 2
_STATE_NAMES = {CLOSED: "closed", OPEN: "open", HALF_OPEN: "half_open"}


class SinkError(RuntimeError):
    """Delivery failed. Retryable unless ``permanent`` is set."""

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


class Sink(Protocol):
    def write(self, payloads: list[dict[str, Any]]) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
    @property
    def breaker_state(self) -> int: ...


class Breaker:
    """Open after ``fails`` consecutive failures; one probe allowed every ``cooldown_s``."""

    def __init__(self, fails: int, cooldown_s: float = 5.0) -> None:
        self.limit = max(1, fails)
        self.cooldown_s = cooldown_s
        self.failures = 0
        self.opened_at = 0.0
        self.state = CLOSED

    def allow(self) -> bool:
        if self.state == OPEN and time.monotonic() - self.opened_at >= self.cooldown_s:
            # One request is let through to find out whether the far end is back.
            self.state = HALF_OPEN
        return self.state != OPEN

    def succeeded(self) -> None:
        if self.state != CLOSED:
            log.info("breaker closed")
        self.failures = 0
        self.state = CLOSED

    def failed(self) -> None:
        self.failures += 1
        if self.failures >= self.limit and self.state != OPEN:
            self.state = OPEN
            self.opened_at = time.monotonic()
            log.error("breaker opened", extra={"failures": self.failures})
        elif self.state == HALF_OPEN:
            self.state = OPEN
            self.opened_at = time.monotonic()

    @property
    def name(self) -> str:
        return _STATE_NAMES[self.state]


class NdjsonFileSink:
    """Append one JSON object per line, fsync on a timer, rotate by size."""

    def __init__(
        self, path: Path, *, fsync_ms: int = 200, rotate_bytes: int = 64 * 1024 * 1024
    ) -> None:
        self.path = Path(path)
        self.fsync_ms = fsync_ms
        self.rotate_bytes = rotate_bytes
        self._handle: Any = None
        self._last_fsync = time.monotonic()
        self._written = 0

    # ---------------------------------------------------------------- lifecycle
    def _open(self) -> Any:
        if self._handle is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("ab")
            self._written = self.path.stat().st_size
        return self._handle

    def write(self, payloads: list[dict[str, Any]]) -> None:
        handle = self._open()
        blob = b"".join(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
            for payload in payloads
        )
        try:
            handle.write(blob)
        except OSError as exc:
            raise SinkError(f"{self.path}: {exc}") from exc
        self._written += len(blob)
        if self._written >= self.rotate_bytes:
            self._rotate()

    def flush(self) -> None:
        if self._handle is None:
            return
        self._handle.flush()
        if (time.monotonic() - self._last_fsync) * 1000 >= self.fsync_ms:
            import os

            os.fsync(self._handle.fileno())
            self._last_fsync = time.monotonic()

    def _rotate(self) -> None:
        """Copy the content aside and truncate in place, so the live inode never changes.

        Wazuh's `<localfile>` holds this file open. Renaming it would leave the manager reading a
        file nothing writes to any more — no error, no alerts, until someone restarted it.
        """
        assert self._handle is not None
        self.flush()
        backup = self.path.with_suffix(self.path.suffix + ".1")
        try:
            backup.write_bytes(self.path.read_bytes())
            self._handle.truncate(0)
            self._handle.seek(0)
            self._written = 0
            log.info("rotated sink file", extra={"path": str(self.path), "backup": str(backup)})
        except OSError as exc:
            # A failed rotation must not lose the event in hand; keep appending and try again later.
            log.error("could not rotate sink file", extra={"error": str(exc)})

    def close(self) -> None:
        if self._handle is not None:
            self.flush()
            self._handle.close()
            self._handle = None

    @property
    def breaker_state(self) -> int:
        return CLOSED  # a local file has no far end to lose


class SyslogTcpSink:
    """RFC 5425 octet-counted frames over TCP, with reconnect and a breaker."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        breaker_fails: int = 5,
        connect_timeout: float = 5.0,
        cooldown_s: float = 5.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.breaker = Breaker(breaker_fails, cooldown_s=cooldown_s)
        self._socket: socket.socket | None = None
        self._backoff = 0.5

    def _connect(self) -> socket.socket:
        if self._socket is not None:
            return self._socket
        try:
            self._socket = socket.create_connection(
                (self.host, self.port), timeout=self.connect_timeout
            )
        except OSError as exc:
            raise SinkError(f"{self.host}:{self.port}: {exc}") from exc
        self._backoff = 0.5
        return self._socket

    def write(self, payloads: list[dict[str, Any]]) -> None:
        if not self.breaker.allow():
            raise SinkError(f"breaker open for {self.host}:{self.port}")
        try:
            sock = self._connect()
            for payload in payloads:
                body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
                # RFC 5425: the length prefix is the only framing that survives a JSON body.
                sock.sendall(f"{len(body)} ".encode("ascii") + body)
        except (SinkError, OSError) as exc:
            self._drop()
            self.breaker.failed()
            # Backoff is the caller's wait, not a sleep inside the write: the worker thread decides.
            self._backoff = min(self._backoff * 2, 30.0)
            raise SinkError(f"{self.host}:{self.port}: {exc}") from exc
        self.breaker.succeeded()

    def flush(self) -> None:
        return None  # sendall already handed the bytes to the kernel

    def _drop(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None

    def close(self) -> None:
        self._drop()

    @property
    def retry_after(self) -> float:
        return self._backoff

    @property
    def breaker_state(self) -> int:
        return self.breaker.state


class HttpJsonSink:
    """POST a JSON array per batch."""

    def __init__(
        self, url: str, *, breaker_fails: int = 5, timeout: float = 10.0, cooldown_s: float = 5.0
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.breaker = Breaker(breaker_fails, cooldown_s=cooldown_s)

    def write(self, payloads: list[dict[str, Any]]) -> None:
        if not self.breaker.allow():
            raise SinkError(f"breaker open for {self.url}")
        body = json.dumps(payloads, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.status >= 400:
                    raise SinkError(f"{self.url}: HTTP {response.status}")
        except (urllib.error.URLError, OSError, SinkError) as exc:
            self.breaker.failed()
            raise SinkError(f"{self.url}: {exc}") from exc
        self.breaker.succeeded()

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    @property
    def breaker_state(self) -> int:
        return self.breaker.state


def build_sink(spec: dict[str, Any], *, cfg: Any) -> Sink:
    """Build the sink a route declares. Unknown types are refused at load, not per event."""
    kind = str(spec.get("type", ""))
    if kind == "ndjson_file":
        path = spec.get("path")
        if not path:
            raise ValueError("ndjson_file needs a path")
        return NdjsonFileSink(
            Path(str(path)),
            fsync_ms=int(spec.get("fsync_ms", cfg.route_fsync_ms)),
            rotate_bytes=int(spec.get("rotate_bytes", cfg.sink_rotate_bytes)),
        )
    if kind == "syslog_tcp":
        host = str(spec.get("host") or cfg.wazuh_remote_host)
        port = int(spec.get("port") or cfg.wazuh_remote_port)
        if not host:
            raise ValueError("syslog_tcp needs a host (or VEYRA_WAZUH_REMOTE_HOST)")
        return SyslogTcpSink(host, port, breaker_fails=cfg.route_breaker_fails)
    if kind == "http_json":
        url = spec.get("url")
        if not url:
            raise ValueError("http_json needs a url")
        return HttpJsonSink(str(url), breaker_fails=cfg.route_breaker_fails)
    raise ValueError(f"unknown sink type {kind!r}; known: ndjson_file, syslog_tcp, http_json")
