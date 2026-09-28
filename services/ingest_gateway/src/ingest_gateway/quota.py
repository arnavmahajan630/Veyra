"""Per-source rate limiting: a token bucket, one per source.

The bucket holds one second's worth of events (`quota_eps`), which is the behaviour AC3 describes:
a source rated at 5 EPS that sends 50 in one second gets about 5 through and 429s for the rest,
rather than being cut off entirely or allowed to burst a minute's budget at once.

Time comes from `time.monotonic()`, so a clock adjustment mid-demo cannot hand out free tokens.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# Float slack. Refilling is `elapsed * rate`, and 0.4 s at 5 EPS comes out as 1.9999999999998863,
# so an exact comparison would 429 a source that is sending exactly its quota — the most common
# case there is. One nanosecond's worth of tokens is far below anything a quota can express.
EPSILON = 1e-9


@dataclass(slots=True)
class Bucket:
    capacity: float
    tokens: float
    rate: float
    updated: float


class QuotaLimiter:
    """Thread-safe token buckets keyed by ``source_id``."""

    def __init__(self, *, clock: object = None) -> None:
        self._buckets: dict[str, Bucket] = {}
        self._lock = threading.Lock()
        self._clock = clock or time.monotonic

    def allow(self, source_id: str, quota_eps: int, cost: int = 1) -> bool:
        """Take ``cost`` tokens for ``source_id``. False means the caller must answer 429.

        ``cost`` is the number of events in the request: a batch of 40 events against a 5 EPS quota
        is 40 events' worth of work, not one request's worth. A request larger than the whole bucket
        would otherwise be unsendable forever, so it is allowed once the bucket is full, and drains
        it — the quota is then enforced by how long the next request has to wait.
        """
        if quota_eps <= 0:
            return True
        now = float(self._clock())  # type: ignore[operator]
        with self._lock:
            bucket = self._buckets.get(source_id)
            if bucket is None or bucket.capacity != float(quota_eps):
                # A re-issued key may carry a new quota; rebuild rather than keep stale capacity.
                bucket = Bucket(
                    capacity=float(quota_eps),
                    tokens=float(quota_eps),
                    rate=float(quota_eps),
                    updated=now,
                )
                self._buckets[source_id] = bucket
            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.rate)
            bucket.updated = now
            wanted = float(cost)
            if wanted > bucket.capacity:
                if bucket.tokens + EPSILON < bucket.capacity:
                    return False
                bucket.tokens = 0.0
                return True
            if bucket.tokens + EPSILON < wanted:
                return False
            bucket.tokens = max(0.0, bucket.tokens - wanted)
            return True

    def tokens_left(self, source_id: str) -> float:
        with self._lock:
            bucket = self._buckets.get(source_id)
            return bucket.tokens if bucket else 0.0
