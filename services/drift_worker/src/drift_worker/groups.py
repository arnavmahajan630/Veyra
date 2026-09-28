"""Drift groups: DLQ records grouped by ``(source_id, template_sig)`` (IF-TEMPLATE-SIG).

The sig is the deterministic grouping; Drain3's cluster id (``miners.py``) only adds the
display template and the ``related_sigs``. A group keeps its count, first/last seen, and up
to ``max_samples`` distinct masked texts with the event uid each came from, so the drafter
can fetch real bytes for them (C4).

C3 AC3 asks for a restart strategy; this one **persists** the groups (``dump``/``load``)
next to the Drain3 state, rather than rebuilding them from the DLQ's retention window,
which would replay history and reopen items people already dismissed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from veyra_common.models import DlqRecord


@dataclass
class Group:
    source_id: str
    tenant_id: str
    template_sig: str
    count: int = 0
    first_seen: str = ""
    last_seen: str = ""
    samples_masked: list[str] = field(default_factory=list)
    sample_event_uids: list[str] = field(default_factory=list)
    cluster_id: int | None = None
    emitted_count: int = 0
    emitted_at_ns: int = 0


class GroupStore:
    def __init__(self, max_samples: int) -> None:
        self.max_samples = max_samples
        self.groups: dict[tuple[str, str], Group] = {}

    def observe(self, record: DlqRecord, cluster_id: int | None) -> Group:
        key = (record.source_id, record.template_sig)
        group = self.groups.get(key)
        if group is None:
            group = Group(record.source_id, record.tenant_id, record.template_sig)
            group.first_seen = record.produced_at
            self.groups[key] = group
        group.count += 1
        group.first_seen = min(group.first_seen, record.produced_at)
        group.last_seen = max(group.last_seen, record.produced_at)
        if cluster_id is not None:
            group.cluster_id = cluster_id
        text = record.text_masked
        if (
            text
            and text not in group.samples_masked
            and len(group.samples_masked) < self.max_samples
        ):
            group.samples_masked.append(text)
            group.sample_event_uids.append(record.event_uid)
        return group

    def related_sigs(self, group: Group) -> list[str]:
        """Other sigs of the same source that Drain3 put in the same cluster."""
        if group.cluster_id is None:
            return []
        return sorted(
            g.template_sig
            for g in self.groups.values()
            if g.source_id == group.source_id
            and g.cluster_id == group.cluster_id
            and g.template_sig != group.template_sig
        )

    def dump(self) -> dict[str, Any]:
        return {"groups": [asdict(g) for g in self.groups.values()]}

    @classmethod
    def load(cls, data: dict[str, Any], max_samples: int) -> GroupStore:
        store = cls(max_samples)
        for row in data.get("groups", []):
            group = Group(**row)
            store.groups[(group.source_id, group.template_sig)] = group
        return store
