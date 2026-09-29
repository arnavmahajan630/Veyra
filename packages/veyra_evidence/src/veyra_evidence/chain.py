"""IF-CHAIN — the per-partition hash chain over raw records (v1 §8.2, frozen).

The chain is what makes "nothing was inserted, removed or reordered" checkable from the
vault files alone::

    h0 = SHA256("VEYRA-GENESIS" 0x1f topic 0x1f partition)
    hn = SHA256(h(n-1) || raw_sha256(32) || event_uid(16, UUID big-endian) || offset(8, BE))

It runs over ``raw.*`` records in **offset order** per ``(topic, partition)``. The archiver
persists the head in each sealed segment header, so the next segment continues it.

Frozen after S0: ``docs/plan/reference/spec_vectors.py`` is the authority and
``tests/test_chain.py`` reproduces its vectors. Changing anything here is breaking.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable

GENESIS_LABEL = b"VEYRA-GENESIS"
SEP = b"\x1f"


def genesis(topic: str, partition: int, epoch: int = 0) -> bytes:
    """The chain's starting hash for one topic-partition.

    ``epoch`` > 0 restarts a chain after an unrecoverable anomaly (IF-SEGMENT). Epoch 0
    omits the suffix entirely, which is what keeps the frozen vectors valid.
    """
    if partition < 0:
        raise ValueError(f"partition must not be negative: {partition}")
    if epoch < 0:
        raise ValueError(f"epoch must not be negative: {epoch}")
    payload = GENESIS_LABEL + SEP + topic.encode() + SEP + str(partition).encode()
    if epoch:
        payload += SEP + str(epoch).encode()
    return hashlib.sha256(payload).digest()


def step(prev: bytes, raw_sha256: str | bytes, event_uid: str | uuid.UUID, offset: int) -> bytes:
    """Extend the chain by one record. ``prev`` and the result are 32 raw bytes."""
    if len(prev) != 32:
        raise ValueError(f"prev must be 32 bytes, got {len(prev)}")
    if offset < 0:
        raise ValueError(f"offset must not be negative: {offset}")
    digest = bytes.fromhex(raw_sha256) if isinstance(raw_sha256, str) else raw_sha256
    if len(digest) != 32:
        raise ValueError("raw_sha256 must be 32 bytes / 64 hex characters")
    uid = uuid.UUID(event_uid) if isinstance(event_uid, str) else event_uid
    return hashlib.sha256(prev + digest + uid.bytes + offset.to_bytes(8, "big")).digest()


def walk(
    topic: str,
    partition: int,
    records: Iterable[tuple[str | bytes, str | uuid.UUID, int]],
    *,
    start: bytes | None = None,
    epoch: int = 0,
) -> bytes:
    """Fold ``(raw_sha256, event_uid, offset)`` records into a chain head.

    ``start`` continues an existing chain (a previous segment's ``last_chain_hash``);
    without it the walk begins at :func:`genesis`. This is how a verifier recomputes a
    segment's chain from its records and compares the result with the header.
    """
    head = genesis(topic, partition, epoch) if start is None else start
    for raw_sha256, event_uid, offset in records:
        head = step(head, raw_sha256, event_uid, offset)
    return head
