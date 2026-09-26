"""IF-TOPICS — topic names, the category map, and ``create_topics()``.

Partitions, retention and compaction all come from the profile (P5). ``make topics``
calls :func:`create_topics`, which is idempotent: existing topics are left alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from veyra_common.models.norm import Category
from veyra_common.settings import Settings, settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- names
RAW_PREFIX = "raw."
NORM_PREFIX = "norm."
TOPIC_REPLAY_RAW = "replay.raw"
TOPIC_LINEAGE = "lineage"
TOPIC_DLQ = "dlq"
TOPIC_SHADOW = "shadow"
TOPIC_VAULT_INDEX = "vault_index"
TOPIC_RECEIPTS = "receipts"
TOPIC_CONTROL = "control"
TOPIC_AUDIT = "audit"

RAW_PATTERN = "^raw\\..*"
NORM_PATTERN = "^norm\\..*"

CATEGORIES: tuple[Category, ...] = (
    "system",
    "findings",
    "iam",
    "network",
    "discovery",
    "application",
    "uncategorized",
)

# Vendors that exist before anything is onboarded: the seeded library sources, the
# custom app used by the demo, the unregistered bucket and the bench feed.
SEED_VENDORS: tuple[str, ...] = (
    "linux",
    "acme_ngfw",
    "custom",
    "unregistered",
    "bench",
)

# OCSF class_uid -> the norm.<category> topic it lands on (IF-OCSF-SUBSET).
CLASS_CATEGORY: dict[int, Category] = {
    0: "uncategorized",
    1007: "system",
    3002: "iam",
    4001: "network",
    4002: "network",
}


def raw_topic(vendor: str) -> str:
    """``raw.<vendor>``."""
    return f"{RAW_PREFIX}{vendor}"


def norm_topic(category: Category | str) -> str:
    """``norm.<category>``."""
    return f"{NORM_PREFIX}{category}"


def category_for_class(class_uid: int) -> Category:
    """The topic category for an OCSF class; unknown classes go to ``uncategorized``."""
    return CLASS_CATEGORY.get(class_uid, "uncategorized")


# ---------------------------------------------------------------- specs
@dataclass(frozen=True, slots=True)
class TopicSpec:
    """One topic's desired shape."""

    name: str
    partitions: int
    retention_days: int | None  # None => compacted, no time retention
    compacted: bool = False

    def config(self, replication: int) -> dict[str, str]:
        cfg: dict[str, str] = {"min.insync.replicas": "1" if replication < 3 else "2"}
        if self.compacted:
            cfg["cleanup.policy"] = "compact"
            cfg["retention.ms"] = "-1"
            cfg["min.cleanable.dirty.ratio"] = "0.1"
        else:
            cfg["cleanup.policy"] = "delete"
            cfg["retention.ms"] = str(int((self.retention_days or 1) * 86_400_000))
        return cfg


def topic_specs(
    cfg: Settings | None = None, *, vendors: tuple[str, ...] = SEED_VENDORS
) -> list[TopicSpec]:
    """Every topic VEYRA needs, sized from the profile."""
    s = cfg or settings
    raw_parts = s.raw_partitions_per_vendor
    norm_parts = s.norm_partitions
    specs: list[TopicSpec] = []

    for vendor in vendors:
        specs.append(TopicSpec(raw_topic(vendor), raw_parts, s.retention_raw_days))
    specs.append(TopicSpec(TOPIC_REPLAY_RAW, raw_parts, s.retention_replay_days))

    for category in CATEGORIES:
        specs.append(TopicSpec(norm_topic(category), norm_parts, s.retention_norm_days))

    specs += [
        TopicSpec(TOPIC_LINEAGE, norm_parts, s.retention_lineage_days),
        TopicSpec(TOPIC_DLQ, s.dlq_partitions, s.retention_dlq_days),
        TopicSpec(TOPIC_SHADOW, s.shadow_partitions, s.retention_shadow_days),
        TopicSpec(TOPIC_VAULT_INDEX, raw_parts, s.retention_vault_index_days),
        TopicSpec(TOPIC_RECEIPTS, s.receipts_partitions, s.retention_receipts_days),
        TopicSpec(TOPIC_AUDIT, s.audit_partitions, s.retention_audit_days),
        TopicSpec(TOPIC_CONTROL, 1, None, compacted=True),
    ]
    return specs


def create_topics(cfg: Settings | None = None, *, timeout: float = 30.0) -> dict[str, str]:
    """Create every topic that does not exist yet. Returns ``{topic: status}``."""
    from confluent_kafka.admin import AdminClient, NewTopic  # local import: keeps CLI startup light

    s = cfg or settings
    admin = AdminClient({"bootstrap.servers": s.kafka_bootstrap})
    existing = set(admin.list_topics(timeout=timeout).topics)

    specs = topic_specs(s)
    wanted = [spec for spec in specs if spec.name not in existing]
    result = {spec.name: "exists" for spec in specs if spec.name in existing}
    if not wanted:
        return result

    futures = admin.create_topics(
        [
            NewTopic(
                spec.name,
                num_partitions=spec.partitions,
                replication_factor=s.kafka_replication,
                config=spec.config(s.kafka_replication),
            )
            for spec in wanted
        ]
    )
    for name, future in futures.items():
        try:
            future.result(timeout=timeout)
            result[name] = "created"
        except Exception as exc:
            result[name] = f"error: {exc}"
            log.error("topic create failed", extra={"topic": name, "error": str(exc)})
    return result


def main() -> None:
    """``python -m veyra_common.topics`` / ``make topics``."""
    for name, status in sorted(create_topics().items()):
        print(f"{status:>10}  {name}")


if __name__ == "__main__":
    main()
