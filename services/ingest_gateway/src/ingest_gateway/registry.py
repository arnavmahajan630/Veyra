"""Who is allowed to push, and as which source.

HTTP sources are resolved by API key, not by the edge's IP/hostname inventory (IF-INVENTORY says so
explicitly). The registry follows the compacted `control` topic for `apikey:*` and `source:*` — the
same messages control-api publishes when an operator issues a key — so a revocation reaches the
gateway in the time it takes one Kafka message to arrive, with no gateway restart and no shared
database.

Lookup is by **digest**, not by scanning keys: the secret is hashed once with the pepper and the
result is a dict hit, then compared with `hmac.compare_digest`. That keeps the comparison constant
time without making it linear in the number of issued keys.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any

from ingest_gateway.settings import GatewaySettings
from veyra_common.control import ControlReader
from veyra_common.hashing import sha256_hex
from veyra_common.models import KEY_PREFIX_APIKEY, KEY_PREFIX_SOURCE

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Principal:
    """The identity an authenticated request stamps its envelopes with."""

    key_id: str
    source_id: str
    tenant_id: str
    vendor: str
    zone: str
    quota_eps: int
    salt_buckets: int = 1


class AuthFailure(Exception):
    """A request that must be answered with 401. ``reason`` is for logs, never for the client."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class KeyRegistry:
    """API keys and source records, kept current from `control`."""

    def __init__(self, pepper: bytes, *, cfg: GatewaySettings, group: str | None = None) -> None:
        self.pepper = pepper
        self.cfg = cfg
        self._keys: dict[str, dict[str, Any]] = {}  # secret_sha256 -> apikey message
        self._by_id: dict[str, dict[str, Any]] = {}  # key_id -> apikey message
        self._sources: dict[str, dict[str, Any]] = {}
        self.reader = ControlReader(
            group=group or f"{cfg.consumer_group}-control-{cfg.instance}",
            cfg=cfg,
            prefixes=(KEY_PREFIX_APIKEY, KEY_PREFIX_SOURCE),
            on_message=self._handle,
            name="gateway-control",
        )

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        self.reader.start()

    def stop(self) -> None:
        self.reader.stop()

    def wait_ready(self, timeout: float) -> bool:
        return self.reader.wait_ready(timeout)

    @property
    def ready(self) -> bool:
        return self.reader.ready.is_set()

    # ---------------------------------------------------------------- control
    def _handle(self, name: str, payload: dict[str, Any] | None) -> None:
        if name.startswith(KEY_PREFIX_APIKEY):
            key_id = name.removeprefix(KEY_PREFIX_APIKEY)
            previous = self._by_id.pop(key_id, None)
            if previous is not None:
                # Drop the old digest even when the secret is unchanged, so a revoked key can never
                # linger under a stale entry.
                self._keys.pop(str(previous.get("secret_sha256", "")), None)
            if payload is None:  # a tombstone deletes the key outright
                return
            self._by_id[key_id] = payload
            digest = str(payload.get("secret_sha256", ""))
            if digest:
                self._keys[digest] = payload
        elif name.startswith(KEY_PREFIX_SOURCE):
            source_id = name.removeprefix(KEY_PREFIX_SOURCE)
            if payload is None:
                self._sources.pop(source_id, None)
            else:
                self._sources[source_id] = payload

    # ---------------------------------------------------------------- auth
    def digest(self, secret: str) -> str:
        """``sha256(pepper + secret)`` — identical to `control_api.keys.secret_digest`."""
        return sha256_hex(self.pepper + secret.encode("utf-8"))

    def resolve(self, secret: str) -> Principal:
        """The principal for ``secret``, or raise :class:`AuthFailure`."""
        if not secret:
            raise AuthFailure("no credential presented")
        record = self._keys.get(self.digest(secret))
        if record is None:
            raise AuthFailure("unknown key")
        # The dict hit already matched; this is the constant-time confirmation, so a timing
        # difference cannot distinguish "wrong secret" from "wrong length of secret".
        if not hmac.compare_digest(str(record.get("secret_sha256", "")), self.digest(secret)):
            raise AuthFailure("digest mismatch")
        if record.get("status") != "active":
            raise AuthFailure(f"key {record.get('key_id')} is {record.get('status')}")

        source_id = str(record["source_id"])
        source = self._sources.get(source_id)
        if source is None:
            # Onboarding issues the key first; refusing the event here would lose data (P2). The
            # event is still attributed to the right source and tenant — only vendor and zone are
            # defaulted, and the warning says so.
            log.warning(
                "no source record for an authenticated key",
                extra={"source_id": source_id, "key_id": record.get("key_id")},
            )
        if source is not None and source.get("status") == "paused":
            raise AuthFailure(f"source {source_id} is paused")

        return Principal(
            key_id=str(record["key_id"]),
            source_id=source_id,
            tenant_id=str(record.get("tenant_id") or (source or {}).get("tenant_id") or ""),
            vendor=str((source or {}).get("vendor") or self.cfg.default_vendor),
            zone=str((source or {}).get("zone") or self.cfg.default_zone),
            quota_eps=int(record.get("quota_eps") or self.cfg.gateway_default_quota_eps),
            salt_buckets=int((source or {}).get("salt_buckets") or 1),
        )

    # ---------------------------------------------------------------- introspection
    @property
    def key_count(self) -> int:
        return len(self._keys)

    @property
    def source_count(self) -> int:
        return len(self._sources)
