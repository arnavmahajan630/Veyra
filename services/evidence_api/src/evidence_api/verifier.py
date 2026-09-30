"""The 8 verification steps behind ``GET /evidence/{event_uid}/verify`` (IF-API-EVIDENCE).

Each step answers one question, in the order an auditor would ask them, and every step
reuses the primitive that produced the artefact in the first place:

==================  ==========================================================
fetch_raw           the event is in a sealed segment, and the raw bytes decode
decrypt_segment     AES-256-GCM opens the segment and the blob hash matches   (B2)
hash_raw            SHA-256 of the raw bytes equals the envelope's raw_sha256
chain_walk          the per-partition hash chain recomputes to the header head (IF-CHAIN)
segment_digest      the digest recomputes from the header and is the Merkle leaf
merkle_inclusion    that leaf sits under the signed root                       (IF-MERKLE)
root_signature      the root payload carries a valid Ed25519 signature         (IF-SIGNED-ROOT)
immudb_verified     *prototype*: not implemented
==================  ==========================================================

A step that cannot run because an earlier one failed is reported ``ok=false`` with the
reason, never skipped silently — a verification report with a gap in it is worthless.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from evidence_api.locator import EvidenceNotFound, Located, VaultLocator
from veyra_common.hashing import sha256_hex
from veyra_evidence.digest import segment_digest_hex
from veyra_evidence.keys import verify_signature
from veyra_evidence.ledger import canonical_bytes
from veyra_evidence.merkle import inclusion_proof, verify_inclusion
from veyra_evidence.segment import SegmentIntegrityError

STEP_IDS = (
    "fetch_raw",
    "decrypt_segment",
    "hash_raw",
    "chain_walk",
    "segment_digest",
    "merkle_inclusion",
    "root_signature",
    "immudb_verified",
)

LABELS = {
    "fetch_raw": "Raw bytes fetched from the sealed segment",
    "decrypt_segment": "Segment decrypted (AES-256-GCM) and blob hash matches",
    "hash_raw": "SHA-256 of the raw bytes matches the envelope",
    "chain_walk": "Per-partition hash chain recomputes",
    "segment_digest": "Segment digest recomputes from the header",
    "merkle_inclusion": "Segment is included under the signed window root",
    "root_signature": "Window root carries a valid Ed25519 signature",
    "immudb_verified": "Root anchored in immudb",
}

NOT_IMPLEMENTED = "not_implemented"
# The segment is sealed but the Merkle window covering it has not been signed yet. That is
# an ordinary wait of up to ``merkle_window_seconds``, not a verification failure, so the
# console shows these steps grey with the time remaining rather than red.
PENDING_SEAL = "pending_seal"


@dataclass
class Step:
    id: str
    label: str
    ok: bool
    detail: str = ""
    ms: float = 0.0
    status: str | None = None  # only set for the prototype's not_implemented step

    def as_json(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "ok": self.ok,
            "detail": self.detail,
            "ms": round(self.ms, 3),
        }
        if self.status:
            body["status"] = self.status
        return body


@dataclass
class VerifyReport:
    event_uid: str
    steps: list[Step] = field(default_factory=list)
    # Artefacts the export endpoint reuses, so it never re-derives them.
    proof: list[dict[str, Any]] = field(default_factory=list)
    located: Located | None = None

    @property
    def verified(self) -> bool:
        """True when every implemented step passed.

        ``immudb_verified`` is excluded: it is not implemented in this prototype, so
        counting it would make an otherwise sound chain of evidence look broken.
        """
        return all(step.ok for step in self.steps if step.status != NOT_IMPLEMENTED)

    def as_json(self) -> dict[str, Any]:
        return {
            "event_uid": self.event_uid,
            "verified": self.verified,
            "steps": [step.as_json() for step in self.steps],
        }


def _timed(fn: Callable[[], tuple[bool, str]]) -> tuple[bool, str, float]:
    started = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", (time.perf_counter() - started) * 1000
    return ok, detail, (time.perf_counter() - started) * 1000


class Verifier:
    def __init__(self, locator: VaultLocator) -> None:
        self.locator = locator

    def verify(self, event_uid: str) -> VerifyReport:
        report = VerifyReport(event_uid=event_uid)

        def add(step_id: str, fn: Callable[[], tuple[bool, str]]) -> Step:
            ok, detail, ms = _timed(fn)
            step = Step(step_id, LABELS[step_id], ok, detail, ms)
            report.steps.append(step)
            return step

        # ---------------------------------------------------------- 1. fetch_raw
        located: Located | None = None

        def fetch_raw() -> tuple[bool, str]:
            nonlocal located
            located = self.locator.locate(event_uid)
            raw = base64.b64decode(located.envelope["raw_b64"], validate=True)
            return True, (
                f"{len(raw)} bytes from {located.segment_id} record {located.record.record_idx}"
            )

        add("fetch_raw", fetch_raw)
        if located is None:
            # Nothing else can be checked without the segment; say so per step.
            for step_id in STEP_IDS[1:]:
                report.steps.append(
                    Step(
                        step_id,
                        LABELS[step_id],
                        False,
                        "not checked: the event was not found in the vault",
                        0.0,
                        NOT_IMPLEMENTED if step_id == "immudb_verified" else None,
                    )
                )
            return report
        report.located = located
        found = located

        # ------------------------------------------------- 2. decrypt_segment
        def decrypt_segment() -> tuple[bool, str]:
            reader = self.locator.reader(found.path)
            records = list(reader.records())  # raises SegmentIntegrityError on tampering
            return True, (
                f"{len(records)} records, blob_sha256 matches, "
                f"zstd level {found.header['zstd_level']}"
            )

        add("decrypt_segment", decrypt_segment)

        # ------------------------------------------------------- 3. hash_raw
        def hash_raw() -> tuple[bool, str]:
            envelope = found.envelope
            raw = base64.b64decode(envelope["raw_b64"], validate=True)
            actual = sha256_hex(raw)
            claimed = str(envelope["raw_sha256"])
            if actual != claimed:
                return False, f"envelope claims {claimed}, bytes hash to {actual}"
            return True, actual

        add("hash_raw", hash_raw)

        # ----------------------------------------------------- 4. chain_walk
        def chain_walk() -> tuple[bool, str]:
            head = self.locator.reader(found.path).verify_chain()
            return True, f"head {head} over {found.header['record_count']} records"

        add("chain_walk", chain_walk)

        # -------------------------------------------------- 5. segment_digest
        def segment_digest() -> tuple[bool, str]:
            recomputed = segment_digest_hex(found.header)
            if recomputed != found.digest_hex:
                return False, f"header digest {found.digest_hex}, recomputed {recomputed}"
            return True, recomputed

        add("segment_digest", segment_digest)

        # ------------------------------------------------ 6. merkle_inclusion
        entry = found.ledger_entry

        def merkle_inclusion() -> tuple[bool, str]:
            if entry is None:
                return False, "the window holding this segment has not been signed yet"
            leaves, ids = self.locator.window_leaves(entry)
            index = ids.index(found.segment_id)
            proof = inclusion_proof(leaves, index)
            report.proof = [step.as_json() for step in proof]
            root = str(entry.payload["root"])
            if not verify_inclusion(leaves[index], proof, root):
                return False, f"leaf {index} is not under root {root}"
            return True, f"leaf {index} of {len(leaves)} under root {root}"

        inclusion_step = add("merkle_inclusion", merkle_inclusion)

        # ------------------------------------------------- 7. root_signature
        def root_signature() -> tuple[bool, str]:
            if entry is None:
                return False, "no signed root covers this segment yet"
            pem = self.locator.public_key_pem()
            if not verify_signature(pem, canonical_bytes(entry.payload), entry.signature):
                return False, f"Ed25519 verification failed for {entry.window_id}"
            return True, f"{entry.window_id} signed by {entry.payload['key_id']}"

        signature_step = add("root_signature", root_signature)

        # Nothing is wrong with an event whose window has not been signed yet — it is simply
        # early. Mark the two root-dependent steps so the UI can say "sealing in <= N s".
        if entry is None:
            wait_s = int(getattr(self.locator.cfg, "merkle_window_seconds", 60) or 60)
            for step in (inclusion_step, signature_step):
                step.status = PENDING_SEAL
                step.detail = f"{step.detail} (sealing in <= {wait_s} s)"

        # ------------------------------------------------ 8. immudb_verified
        report.steps.append(
            Step(
                "immudb_verified",
                LABELS["immudb_verified"],
                False,
                "not implemented in this prototype: the root is in the local ledger only",
                0.0,
                NOT_IMPLEMENTED,
            )
        )
        return report


__all__ = [
    "NOT_IMPLEMENTED",
    "STEP_IDS",
    "EvidenceNotFound",
    "SegmentIntegrityError",
    "Step",
    "Verifier",
    "VerifyReport",
]
