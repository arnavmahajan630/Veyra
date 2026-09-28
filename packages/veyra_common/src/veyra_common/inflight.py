"""Crash-loop guard: remember which batch we were working on when we died (A4).

:class:`TxnProcessor` already retries a batch that raises and quarantines it after
``poison_max_retries`` attempts. That counter lives in memory, which is enough for an exception —
but not for the failure that actually takes a pipeline down: a record that makes the process *die*
(a segfault in a C extension, the OOM killer, a `sys.exit` deep in a library). The transaction
aborts, the offsets never move, systemd or compose restarts us, the same record arrives, and we die
again. Forever, at whatever rate the restart policy allows.

So the attempt count has to outlive the process. Before each batch we write its coordinates and the
attempt number to ``data/state/<name>_inflight``; after a successful commit we clear the file. On
startup, if the first batch we see is the one already recorded, the count carries on from disk, and
once it passes the limit the batch is quarantined **without being processed at all** — because the
whole point is that processing it is what killed us.

The file is written with the usual write-temp-then-rename, so a crash during the write itself leaves
either the old journal or the new one, never half of one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)

# (topic, partition, first_offset, last_offset) for every partition in the batch, sorted.
BatchId = tuple[tuple[str, int, int, int], ...]


@dataclass(slots=True)
class InflightJournal:
    """One file per service, holding at most one in-flight batch."""

    path: Path
    max_attempts: int

    @classmethod
    def for_service(cls, name: str, *, cfg: Settings | None = None) -> InflightJournal:
        cfg = cfg or settings
        return cls(
            path=cfg.state_dir / f"{name}_inflight",
            max_attempts=cfg.poison_max_retries,
        )

    # ---------------------------------------------------------------- reading
    def _load(self) -> tuple[BatchId | None, int]:
        try:
            payload = json.loads(self.path.read_text())
        except FileNotFoundError:
            return None, 0
        except (OSError, ValueError):
            # An unreadable journal must not stop the service: worst case we lose one attempt
            # count and fall back to the in-memory retry limit.
            log.warning("unreadable inflight journal", extra={"path": str(self.path)})
            return None, 0
        try:
            batch = tuple((str(t), int(p), int(lo), int(hi)) for t, p, lo, hi in payload["batch"])
            return batch, int(payload["attempts"])
        except (KeyError, TypeError, ValueError):
            log.warning("malformed inflight journal", extra={"path": str(self.path)})
            return None, 0

    def recorded(self) -> tuple[BatchId | None, int]:
        """The batch we were last working on, and how many times we have tried it."""
        return self._load()

    # ---------------------------------------------------------------- writing
    def begin(self, batch_id: BatchId) -> int:
        """Record that we are about to process ``batch_id``; return the attempt number.

        The first attempt returns 1. A batch that matches what is already on disk continues that
        count, which is what survives a hard crash.
        """
        previous, attempts = self._load()
        attempts = attempts + 1 if previous == batch_id else 1
        self._write(batch_id, attempts)
        return attempts

    def poisonous(self, batch_id: BatchId) -> bool:
        """True when this batch has already been attempted up to the limit."""
        previous, attempts = self._load()
        return previous == batch_id and attempts >= self.max_attempts

    def clear(self) -> None:
        """Called after a successful commit: there is nothing in flight any more."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:  # pragma: no cover - permissions
            log.warning("cannot clear inflight journal", extra={"error": str(exc)})

    def _write(self, batch_id: BatchId, attempts: int) -> None:
        payload = {"batch": [list(entry) for entry in batch_id], "attempts": attempts}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(payload))
            os.replace(temp, self.path)
        except OSError as exc:
            # No journal is a degraded mode, not a reason to stop normalizing (P2).
            log.warning("cannot write inflight journal", extra={"error": str(exc)})
