"""IF-SEGMENT — the segment digest, which is also the Merkle leaf data (B3 uses it).

::

    SHA256("VEYRA-SEG" 0x1f segment_id 0x1f prev_chain_hash(32) last_chain_hash(32)
           record_count_u64_be blob_sha256(32))

The digest binds a segment's identity, its position in the chain, how many records it
holds and the exact bytes of its plaintext blob. Signing a Merkle root over these
(B3) is what makes the whole vault tamper-evident.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

SEG_LABEL = b"VEYRA-SEG"
SEP = b"\x1f"


def _hash32(value: str | bytes, field: str) -> bytes:
    raw = bytes.fromhex(value) if isinstance(value, str) else value
    if len(raw) != 32:
        raise ValueError(f"{field} must be 32 bytes / 64 hex characters")
    return raw


def segment_digest(header: Mapping[str, Any]) -> bytes:
    """The digest of a sealed segment, from its header (32 raw bytes)."""
    if header["record_count"] < 0:
        raise ValueError("record_count must not be negative")
    return hashlib.sha256(
        SEG_LABEL
        + SEP
        + str(header["segment_id"]).encode()
        + SEP
        + _hash32(header["prev_chain_hash_hex"], "prev_chain_hash_hex")
        + _hash32(header["last_chain_hash_hex"], "last_chain_hash_hex")
        + int(header["record_count"]).to_bytes(8, "big")
        + _hash32(header["blob_sha256_hex"], "blob_sha256_hex")
    ).digest()


def segment_digest_hex(header: Mapping[str, Any]) -> str:
    return segment_digest(header).hex()
