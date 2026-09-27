"""API key material: the pepper, the stored digest, the one-time key card, demo memory.

Stored digest = sha256(pepper_bytes + secret_utf8). The gateway (A2) reads the same
pepper file and must hash identically. That goes in the C1 report's downstream notes.
"""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control_api.tables import Source
from veyra_common.hashing import sha256_hex

# IF-PORTS: fixed interface ports, not tunables.
GATEWAY_PORT = 8088
LISTENER_PORTS = {"dmz-udp": 5514, "dmz-tcp": 5515, "core-udp": 5524, "core-tcp": 5525}
SYSLOG_TRANSPORTS = frozenset({"syslog_udp", "syslog_tcp"})


def ensure_pepper(keys_dir: Path) -> tuple[bytes, str]:
    path = keys_dir / "api_pepper"
    if not path.exists():
        keys_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="ascii")
        path.chmod(0o400)
    pepper = path.read_bytes().strip()
    return pepper, "p_" + sha256_hex(pepper)[:8]


def secret_digest(pepper: bytes, secret: str) -> str:
    return sha256_hex(pepper + secret.encode("utf-8"))


def key_card(*, public_host: str, key_id: str, secret: str, source: Source) -> dict[str, Any]:
    base = f"http://{public_host}:{GATEWAY_PORT}"
    hec_url = f"{base}/services/collector/event"
    syslog = None
    if source.transport in SYSLOG_TRANSPORTS:
        syslog = {
            "host": public_host,
            "port": LISTENER_PORTS.get(source.listener or ""),
            "listener": source.listener,
        }
    curl = (
        f"curl -s {hec_url} -H 'Authorization: Splunk {secret}' "
        """-d '{"event":"<your log line>"}'"""
    )
    return {
        "key_id": key_id,
        "secret": secret,
        "endpoints": {"hec_url": hec_url, "batch_url": f"{base}/v1/batch", "syslog": syslog},
        "curl_example": curl,
    }


@dataclass(frozen=True)
class IssuedKey:
    key_id: str
    secret: str
    source_id: str


class LastKeyMemory:
    """Demo mode only: the last issued key, for GET /internal/demo/last-key (B7)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: IssuedKey | None = None

    def remember(self, key: IssuedKey) -> None:
        with self._lock:
            self._last = key

    def get(self) -> IssuedKey | None:
        with self._lock:
            return self._last

    def forget(self) -> None:
        with self._lock:
            self._last = None
