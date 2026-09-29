"""IF-MERKLE — the window tree over segment digests (frozen; RFC 6962 hashing).

::

    leaf = SHA256(0x00 || data)
    node = SHA256(0x01 || left || right)
    split point: k = the largest power of 2 less than n

Leaves are the :func:`~veyra_evidence.digest.segment_digest` values of the segments sealed
within ``[window_start, window_end)``, ordered by ``(sealed_at, segment_id)``. One signed
root per window (IF-SIGNED-ROOT) makes every segment in it tamper-evident, and an
inclusion proof shows one segment belongs under that root without revealing the others.

An empty window still has a root — ``SHA256("")`` — so the chain of windows has no gaps.

Frozen after S0: ``docs/plan/reference/spec_vectors.py`` is the authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"


def leaf_hash(data: bytes) -> bytes:
    """``SHA256(0x00 || data)``."""
    return hashlib.sha256(LEAF_PREFIX + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """``SHA256(0x01 || left || right)``."""
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def empty_root() -> bytes:
    """RFC 6962's ``MTH({}) = SHA256("")`` — the root of an empty window."""
    return hashlib.sha256(b"").digest()


def _split(n: int) -> int:
    """The largest power of 2 strictly less than ``n`` (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def root(leaves: Sequence[bytes]) -> bytes:
    """The Merkle tree hash over ``leaves`` (each item is leaf *data*, not a leaf hash)."""
    if not leaves:
        return empty_root()
    if len(leaves) == 1:
        return leaf_hash(leaves[0])
    k = _split(len(leaves))
    return node_hash(root(leaves[:k]), root(leaves[k:]))


def root_hex(leaves: Sequence[bytes]) -> str:
    return root(leaves).hex()


@dataclass(frozen=True, slots=True)
class ProofStep:
    """One sibling on the path from a leaf to the root."""

    hash: bytes
    # True when the sibling sits on the left, i.e. the running hash is the right child.
    sibling_is_left: bool

    def as_json(self) -> dict[str, object]:
        return {"hash": self.hash.hex(), "sibling_is_left": self.sibling_is_left}

    @classmethod
    def from_json(cls, data: dict[str, object]) -> ProofStep:
        return cls(bytes.fromhex(str(data["hash"])), bool(data["sibling_is_left"]))


def inclusion_proof(leaves: Sequence[bytes], index: int) -> list[ProofStep]:
    """The audit path proving ``leaves[index]`` is under :func:`root`, leaf-first."""
    if not 0 <= index < len(leaves):
        raise IndexError(f"index {index} out of range for {len(leaves)} leaves")
    if len(leaves) == 1:
        return []
    k = _split(len(leaves))
    if index < k:
        # The sibling is the whole right subtree.
        return [*inclusion_proof(leaves[:k], index), ProofStep(root(leaves[k:]), False)]
    return [*inclusion_proof(leaves[k:], index - k), ProofStep(root(leaves[:k]), True)]


def verify_inclusion(
    leaf_data: bytes, proof: Sequence[ProofStep], expected_root: bytes | str
) -> bool:
    """Recompute the root from one leaf and its audit path, and compare.

    This is what an auditor runs with nothing but the segment, the proof and the signed
    root — no access to the rest of the vault.
    """
    want = bytes.fromhex(expected_root) if isinstance(expected_root, str) else expected_root
    running = leaf_hash(leaf_data)
    for stepping in proof:
        running = (
            node_hash(stepping.hash, running)
            if stepping.sibling_is_left
            else node_hash(running, stepping.hash)
        )
    return running == want
