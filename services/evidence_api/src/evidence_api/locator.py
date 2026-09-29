"""Find one event's evidence in the vault (B4 prototype).

Everything the API serves comes from artefacts the earlier phases already produce:

* the sealed segments the archiver wrote (B2) — the raw envelope and the hash chain;
* the signed-root ledger the integrity service wrote (B3) — the Merkle root and signature.

**Lookup strategy.** B1 indexes ``event_uid -> (segment_id, record_idx)`` in ClickHouse
(``vault_locations``), which is the O(1) path the full B4 uses. This prototype instead
scans the vault, because that keeps the API (and its tests) runnable with nothing but a
data directory — no Kafka, no ClickHouse, no indexer. Segment headers are read first
(cheap, no decryption) and a segment is only decrypted while looking for the record.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veyra_common.settings import Settings
from veyra_evidence.keys import KeyProvider
from veyra_evidence.ledger import LedgerEntry, ledger_path, read_ledger
from veyra_evidence.segment import SEGMENT_SUFFIX, Record, SegmentReader

log = logging.getLogger(__name__)


class EvidenceNotFound(Exception):
    """No sealed segment holds this event."""


@dataclass(frozen=True, slots=True)
class Located:
    """One event, the segment holding it, and the signed root covering that segment."""

    event_uid: str
    path: Path
    header: dict[str, Any]
    record: Record
    digest_hex: str
    # None when the window holding this segment has not been signed yet.
    ledger_entry: LedgerEntry | None

    @property
    def envelope(self) -> dict[str, Any]:
        return json.loads(self.record.envelope_bytes)

    @property
    def segment_id(self) -> str:
        return str(self.header["segment_id"])

    @property
    def raw_sha256(self) -> str:
        return str(self.envelope["raw_sha256"])

    def summary(self) -> dict[str, Any]:
        """The ``GET /evidence/{event_uid}`` body."""
        envelope = self.envelope
        return {
            "event_uid": self.event_uid,
            "raw_sha256": envelope["raw_sha256"],
            "raw_len": envelope["raw_len"],
            "received_time": envelope["received_time"],
            "tenant_id": envelope["tenant_id"],
            "source_id": envelope["source_id"],
            "vendor": envelope["vendor"],
            "zone": envelope["zone"],
            "raw_ref": {
                "topic": self.header["topic"],
                "partition": self.header["partition"],
                "offset": self.record.offset,
            },
            "vault": {
                "segment_id": self.segment_id,
                "record_idx": self.record.record_idx,
                "chain_hash": self.record.chain_hash_hex,
                "segment_digest": self.digest_hex,
                "sealed_at": self.header["sealed_at"],
                "path": str(self.path),
            },
            "signed_root": (
                None
                if self.ledger_entry is None
                else {
                    "window_id": self.ledger_entry.window_id,
                    "root": self.ledger_entry.payload["root"],
                    "leaf_count": self.ledger_entry.payload["leaf_count"],
                }
            ),
        }


class VaultLocator:
    """Reads the vault and the ledger. Holds no state, so nothing can go stale."""

    def __init__(self, cfg: Settings, keys: KeyProvider) -> None:
        self.cfg = cfg
        self.keys = keys

    # ---------------------------------------------------------------- segments
    @property
    def vault_dir(self) -> Path:
        return Path(self.cfg.vault_dir)

    def segment_paths(self) -> list[Path]:
        """Every sealed segment, newest first — recent events are what get looked up."""
        limit = getattr(self.cfg, "evidence_max_segments_scanned", 5000)
        paths = sorted(self.vault_dir.glob(f"*/*/*{SEGMENT_SUFFIX}"), reverse=True)
        return paths[:limit]

    def find_segment(self, segment_id: str) -> Path | None:
        for path in self.segment_paths():
            if path.stem == segment_id:
                return path
        return None

    def reader(self, path: Path) -> SegmentReader:
        return SegmentReader(path, self.keys, cfg=self.cfg)

    # ---------------------------------------------------------------- lookup
    def locate(self, event_uid: str) -> Located:
        """Find the segment and record holding ``event_uid``."""
        uid = event_uid.strip()
        for path in self.segment_paths():
            try:
                reader = self.reader(path)
                header = reader.header()
                for record in reader.records():
                    if json.loads(record.envelope_bytes).get("event_uid") == uid:
                        digest = reader.digest_hex()
                        return Located(
                            event_uid=uid,
                            path=path,
                            header=header,
                            record=record,
                            digest_hex=digest,
                            ledger_entry=self.root_for_segment(str(header["segment_id"])),
                        )
            except Exception:
                # A corrupt segment must not hide the events in the healthy ones; the
                # verify endpoint is where a damaged segment gets reported properly.
                log.warning("skipping an unreadable segment", extra={"path": str(path)})
        raise EvidenceNotFound(f"no sealed segment holds event {uid!r}")

    # ---------------------------------------------------------------- ledger
    @property
    def ledger(self) -> Path:
        return ledger_path(self.cfg)

    def entries(self) -> list[LedgerEntry]:
        return read_ledger(self.ledger)

    def root_for_segment(self, segment_id: str) -> LedgerEntry | None:
        """The signed root whose window covers this segment, if it has been signed."""
        for entry in self.entries():
            if segment_id in entry.payload.get("segments", []):
                return entry
        return None

    def window_leaves(self, entry: LedgerEntry) -> tuple[list[bytes], list[str]]:
        """The window's Merkle leaves in signed order, plus the segment ids.

        Digests come from each segment's header, so a tampered header changes the leaf
        and the inclusion proof stops matching the signed root.
        """
        ids = [str(s) for s in entry.payload.get("segments", [])]
        leaves: list[bytes] = []
        for segment_id in ids:
            path = self.find_segment(segment_id)
            if path is None:
                raise EvidenceNotFound(f"segment {segment_id} named by the root is missing")
            leaves.append(bytes.fromhex(self.reader(path).digest_hex()))
        return leaves, ids

    def public_key_pem(self) -> str:
        key_id = getattr(self.keys, "signing_key_id", None)
        if key_id is None:  # pragma: no cover - only a custom provider hits this
            raise RuntimeError("the key provider does not expose a signing key id")
        return self.keys.public_key_pem(str(key_id))
