"""The per-route HMAC key.

`data/keys/route_hmac`, 32 random bytes, created on first boot and never overwritten — losing it
changes every ``h_…`` a partner has already correlated on, which looks to them as though every
identity in the feed was replaced at once.

This is deliberately **not** B's `LocalKeyProvider`: that owns the vault KEK and the signing key,
whose rotation and custody rules are B3's business. A route key is the router's own secret and has
no relationship to the evidence chain.

Each route derives its own key from the file (`HMAC(master, route_id)`), so two routes never produce
the same ``h_…`` for the same user — one partner cannot join their feed against another's.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from hashlib import sha256
from pathlib import Path

log = logging.getLogger(__name__)

KEY_FILE = "route_hmac"
KEY_BYTES = 32


def ensure_route_key(keys_dir: Path) -> bytes:
    """Read the master route key, creating it on first boot."""
    path = keys_dir / KEY_FILE
    if path.exists():
        material = path.read_bytes()
        if len(material) < KEY_BYTES:
            raise ValueError(f"{path} is only {len(material)} bytes; expected {KEY_BYTES}")
        return material
    keys_dir.mkdir(parents=True, exist_ok=True)
    material = secrets.token_bytes(KEY_BYTES)
    path.write_bytes(material)
    path.chmod(0o400)
    log.info("created a route hmac key", extra={"path": str(path)})
    return material


def route_key(master: bytes, route_id: str) -> bytes:
    """Per-route subkey, so one partner's ``h_…`` values cannot be joined against another's."""
    return hmac.new(master, route_id.encode("utf-8"), sha256).digest()
