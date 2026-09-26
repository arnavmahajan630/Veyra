"""Identifier and clock helpers (IF-NAMING).

UUIDv7 is used for ``event_uid`` so ids sort by creation time, which keeps the
ClickHouse and vault layouts time-local. Nothing in the hot path may call these
helpers *after* stamping: the engine is pure and reads time only from the envelope.
"""

from __future__ import annotations

import secrets
import time
import uuid

import uuid_utils

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def uuid7() -> uuid.UUID:
    """A UUIDv7 as a stdlib ``uuid.UUID`` (so ``.bytes`` feeds IF-CHAIN directly)."""
    return uuid.UUID(str(uuid_utils.uuid7()))


def uuid7_str() -> str:
    """A UUIDv7 as a lowercase string — the ``event_uid`` wire form."""
    return str(uuid_utils.uuid7())


def now_ns() -> int:
    """Wall-clock nanoseconds since the epoch (UTC)."""
    return time.time_ns()


def monotonic_us() -> int:
    """Monotonic microseconds, for budgets and latency histograms."""
    return time.perf_counter_ns() // 1000


def new_api_key_id() -> str:
    """``k_<8 base32>`` (IF-NAMING)."""
    return "k_" + "".join(secrets.choice(_B32) for _ in range(8))


def new_api_key_secret() -> str:
    """``veyra_<32 base62>`` — shown once, stored as sha256(pepper||secret)."""
    return "veyra_" + "".join(secrets.choice(_B62) for _ in range(32))


def segment_id(topic: str, partition: int, first_offset: int) -> str:
    """``seg_<topic>_<partition>_<first_offset zero-padded to 12>`` (IF-NAMING)."""
    return f"seg_{topic}_{partition}_{first_offset:012d}"


def window_id(window_start_unix: int) -> str:
    """``w_<unix_start>`` (IF-NAMING)."""
    return f"w_{window_start_unix}"
