"""Sealed segments -> signed, chained window roots (B3 prototype, v1 §8.3).

The archiver (B2) leaves sealed segments under ``data/vault/<topic>/<partition>/``. This
service reads their headers, groups them into ``VEYRA_MERKLE_WINDOW_SECONDS`` windows by
``sealed_at``, and for each closed window:

1. builds the Merkle tree over the segment digests, ordered by ``(sealed_at, segment_id)``
   (IF-MERKLE);
2. writes an IF-SIGNED-ROOT payload carrying the previous entry's hash;
3. signs the canonical payload with the Ed25519 root key;
4. appends it to the ledger.

Windows are only signed once they are **closed** (``window_end + lag <= now``), so a
segment sealed near a boundary is not left out of the root that should contain it. A
window already in the ledger is never signed again, which makes a restart a no-op.

Prototype scope: no immudb, no ClickHouse, no late-event repair, no crash injection.
Empty windows are skipped rather than signed — IF-SIGNED-ROOT wants a root for every
window, and closing that gap is part of the full B3.
"""

from __future__ import annotations

import base64
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge

from integrity.settings import IntegritySettings
from veyra_common.models import SignedRoot
from veyra_evidence.keys import KeyProvider, LocalKeyProvider, get_key_provider
from veyra_evidence.ledger import (
    GENESIS_PREV,
    LedgerEntry,
    append_entry,
    canonical_bytes,
    last_entry,
    ledger_path,
    payload_sha256,
    read_ledger,
    window_id,
)
from veyra_evidence.merkle import root_hex
from veyra_evidence.segment import SEGMENT_SUFFIX, SegmentReader

log = logging.getLogger(__name__)

ROOTS_SIGNED = Counter("veyra_integrity_roots_signed_total", "Window roots signed")
SEGMENTS_COVERED = Counter(
    "veyra_integrity_segments_covered_total", "Segments included in a signed root"
)
LAST_WINDOW_END = Gauge(
    "veyra_integrity_last_window_end_seconds", "window_end of the newest signed root"
)


@dataclass(frozen=True, slots=True)
class SegmentInfo:
    """What a window needs from one sealed segment."""

    segment_id: str
    digest_hex: str
    sealed_at: float

    @property
    def leaf(self) -> bytes:
        return bytes.fromhex(self.digest_hex)


def parse_sealed_at(value: str) -> float:
    """``sealed_at`` (RFC3339, from the segment header) as a unix timestamp."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


class Integrity:
    def __init__(self, cfg: IntegritySettings, keys: KeyProvider | None = None) -> None:
        self.cfg = cfg
        self.keys = keys or get_key_provider(cfg)
        self.ledger = ledger_path(cfg)
        self.window_seconds = max(1, int(cfg.merkle_window_seconds))
        self._stop = False
        self.roots_signed = 0

    def stop(self) -> None:
        self._stop = True

    # ---------------------------------------------------------------- discovery
    def scan_segments(self) -> list[SegmentInfo]:
        """Every sealed segment in the vault, with its digest and seal time."""
        vault = Path(self.cfg.vault_dir)
        found: list[SegmentInfo] = []
        for path in sorted(vault.glob(f"*/*/*{SEGMENT_SUFFIX}")):
            try:
                reader = SegmentReader(path, self.keys, cfg=self.cfg)
                header = reader.header()
                found.append(
                    SegmentInfo(
                        segment_id=str(header["segment_id"]),
                        # The digest comes from the header, so this needs no decryption.
                        digest_hex=reader.digest_hex(),
                        sealed_at=parse_sealed_at(str(header["sealed_at"])),
                    )
                )
            except Exception:
                log.exception("skipping an unreadable segment", extra={"path": str(path)})
        return found

    def window_start_of(self, sealed_at: float) -> int:
        return int(sealed_at // self.window_seconds) * self.window_seconds

    def group_windows(self, segments: list[SegmentInfo]) -> dict[int, list[SegmentInfo]]:
        """Segments by window start, each window ordered by ``(sealed_at, segment_id)``."""
        windows: dict[int, list[SegmentInfo]] = defaultdict(list)
        for info in segments:
            windows[self.window_start_of(info.sealed_at)].append(info)
        for members in windows.values():
            members.sort(key=lambda s: (s.sealed_at, s.segment_id))
        return dict(windows)

    def signed_window_ids(self) -> set[str]:
        return {entry.window_id for entry in read_ledger(self.ledger)}

    # ---------------------------------------------------------------- signing
    def build_payload(self, window_start: int, members: list[SegmentInfo]) -> SignedRoot:
        """The IF-SIGNED-ROOT document for one window, chained onto the ledger's tail."""
        previous = last_entry(self.ledger)
        prev_hash = previous.sha256 if previous is not None else GENESIS_PREV
        return SignedRoot(
            window_id=window_id(window_start),
            window_start=window_start,
            window_end=window_start + self.window_seconds,
            leaf_count=len(members),
            root=root_hex([m.leaf for m in members]),
            segments=[m.segment_id for m in members],
            prev_signed_sha256=prev_hash,
            key_id=self._signing_key_id(),
        )

    def _signing_key_id(self) -> str:
        key_id = getattr(self.keys, "signing_key_id", None)
        if key_id is None:  # pragma: no cover - only a custom provider hits this
            raise RuntimeError("the key provider does not expose a signing key id")
        return str(key_id)

    def sign_window(self, window_start: int, members: list[SegmentInfo]) -> LedgerEntry:
        """Sign one window and append it. The ledger is the only writer of record."""
        payload = self.build_payload(window_start, members)
        signature = self.keys.sign(payload.key_id, canonical_bytes(payload))
        entry = LedgerEntry(payload.model_dump(mode="json"), base64.b64encode(signature).decode())
        append_entry(self.ledger, entry)
        self.roots_signed += 1
        ROOTS_SIGNED.inc()
        SEGMENTS_COVERED.inc(len(members))
        LAST_WINDOW_END.set(payload.window_end)
        log.info(
            "window root signed",
            extra={
                "window_id": payload.window_id,
                "leaf_count": payload.leaf_count,
                "root": payload.root,
                "prev_signed_sha256": payload.prev_signed_sha256,
            },
        )
        return entry

    def closed_before(self, now: float | None = None) -> float:
        """Windows ending at or before this instant are safe to sign."""
        moment = time.time() if now is None else now
        return moment - self.cfg.integrity_window_lag_seconds

    def run_once(self, now: float | None = None) -> list[LedgerEntry]:
        """Sign every closed, unsigned, non-empty window. Returns the new entries."""
        windows = self.group_windows(self.scan_segments())
        already = self.signed_window_ids()
        cutoff = self.closed_before(now)
        new: list[LedgerEntry] = []
        for window_start in sorted(windows):
            if window_start + self.window_seconds > cutoff:
                continue  # still open
            if window_id(window_start) in already:
                continue  # already signed: a restart must not sign it twice
            new.append(self.sign_window(window_start, windows[window_start]))
        return new

    def run(self) -> None:
        log.info(
            "integrity starting",
            extra={"ledger": str(self.ledger), "window_seconds": self.window_seconds},
        )
        while not self._stop:
            try:
                self.run_once()
            except Exception:
                log.exception("a signing pass failed; retrying on the next tick")
            deadline = time.monotonic() + self.cfg.integrity_scan_seconds
            while not self._stop and time.monotonic() < deadline:
                time.sleep(0.2)
        log.info("integrity stopped", extra={"roots_signed": self.roots_signed})

    # ---------------------------------------------------------------- helpers
    def public_key_pem(self) -> str:
        return self.keys.public_key_pem(self._signing_key_id())

    def stats(self) -> dict[str, Any]:
        return {"roots_signed": self.roots_signed, "ledger": str(self.ledger)}


def ensure_keys(cfg: IntegritySettings) -> LocalKeyProvider:
    """Create the signing key before the first pass, so a failure is loud and early."""
    keys = get_key_provider(cfg)
    keys.ensure_signing_key()
    return keys


def payload_hash(payload: dict[str, Any]) -> str:
    """Re-exported for the audit tool and tests."""
    return payload_sha256(payload)
