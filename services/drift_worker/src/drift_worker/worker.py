"""The drift worker's logic, independent of Kafka and HTTP (C3).

``handle`` takes one IF-DLQ record; ``tick`` emits every group that is due to
control-api's ``POST /internal/drift``:

- a group is due once ``count >= VEYRA_DRIFT_MIN_CLUSTER`` and it has grown since its last
  emission, at most once per ``VEYRA_DRIFT_DEBOUNCE_MS``;
- ``tick(force=True)`` (``POST /flush``, B7's stage 4 fallback) emits every group that has
  grown, whatever its size, without waiting for the debounce.

A failed post keeps the group due, so the next tick retries it. ``checkpoint`` saves the
groups and the Drain3 state together; the Kafka offsets are committed only after it.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge

from drift_worker.groups import Group, GroupStore
from drift_worker.miners import MinerPool
from veyra_common.models import UNREGISTERED_SOURCE, DlqRecord
from veyra_common.settings import Settings

log = logging.getLogger(__name__)

GROUPS = Gauge("veyra_drift_groups", "drift groups the worker is tracking")
EMITTED = Counter("veyra_drift_items_emitted_total", "drift item upserts sent to control-api")

Post = Callable[[dict[str, Any]], None]
NS_PER_MS = 1_000_000


class DriftWorker:
    def __init__(self, cfg: Settings, post: Post, *, clock: Callable[[], int] = time.time_ns):
        self.cfg = cfg
        self._post = post
        self._clock = clock
        self._lock = threading.Lock()
        self._state_file = cfg.state_dir / "drift" / "groups.json"
        self.miners = MinerPool(
            cfg.state_dir / "drain3", sim_th=cfg.drift_drain_sim_th, depth=cfg.drift_drain_depth
        )
        self.store = self._load()

    def _load(self) -> GroupStore:
        if self._state_file.is_file():
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            return GroupStore.load(data, self.cfg.drift_max_samples)
        return GroupStore(self.cfg.drift_max_samples)

    def handle(self, record: DlqRecord) -> Group | None:
        if record.source_id == UNREGISTERED_SOURCE:
            return None  # unregistered traffic is onboarding's business, not drift
        with self._lock:
            cluster = (
                self.miners.add(record.source_id, record.text_masked)
                if record.text_masked
                else None
            )
            group = self.store.observe(record, cluster)
            GROUPS.set(len(self.store.groups))
            return group

    def _due(self, group: Group, now_ns: int, force: bool) -> bool:
        if group.count <= group.emitted_count:
            return False
        if force:
            return True
        waited = now_ns - group.emitted_at_ns >= self.cfg.drift_debounce_ms * NS_PER_MS
        return group.count >= self.cfg.drift_min_cluster and waited

    def payload(self, group: Group) -> dict[str, Any]:
        drain = (
            self.miners.template(group.source_id, group.cluster_id)
            if group.cluster_id is not None
            else ""
        )
        return {
            "source_id": group.source_id,
            "template_sig": group.template_sig,
            "related_sigs": self.store.related_sigs(group),
            "drain_template": drain,
            "count": group.count,
            "samples_masked": list(group.samples_masked),
            "sample_event_uids": list(group.sample_event_uids),
            "first_seen": group.first_seen,
            "last_seen": group.last_seen,
        }

    def tick(self, *, force: bool = False) -> int:
        """Emit every due group; returns how many were accepted."""
        with self._lock:
            now = self._clock()
            due = [g for g in self.store.groups.values() if self._due(g, now, force)]
            sent = 0
            for group in due:
                try:
                    self._post(self.payload(group))
                except Exception:
                    log.exception("drift upsert failed", extra={"sig": group.template_sig})
                    continue
                group.emitted_count, group.emitted_at_ns = group.count, now
                EMITTED.inc()
                sent += 1
            return sent

    def checkpoint(self) -> None:
        """Save groups and Drain3 state; the caller commits Kafka offsets afterwards."""
        with self._lock:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.store.dump()), encoding="utf-8")
            os.replace(tmp, self._state_file)
            self.miners.save()

    def reset(self) -> None:
        """Forget every group and cluster (demo reset). Offsets stay where they are."""
        with self._lock:
            self.store = GroupStore(self.cfg.drift_max_samples)
            self.miners.reset()
            Path(self._state_file).unlink(missing_ok=True)
            GROUPS.set(0)

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self.payload(g) for g in self.store.groups.values()]
