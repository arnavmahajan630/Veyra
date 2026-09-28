"""Contract diff (C2): what changed between two versions, semantically and as YAML.

The semantic diff compares the *compiled* contracts, so formatting, comments and key
order never show up as changes; a template counts as changed when its regex, class,
activity, map or unmapped list differs.
"""

from __future__ import annotations

import difflib
from typing import Any

_TOP = ("envelope", "time", "required", "pii", "enrich", "vocab", "sources")


def _map(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["ocsf_path"]: entry for entry in template.get("map", [])}


def _template_change(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any] | None:
    old_map, new_map = _map(old), _map(new)
    change = {
        "id": new["id"],
        "pattern_changed": old["regex"] != new["regex"],
        "class_changed": (old["class_uid"], old["activity_id"])
        != (new["class_uid"], new["activity_id"]),
        "map_added": sorted(set(new_map) - set(old_map)),
        "map_removed": sorted(set(old_map) - set(new_map)),
        "map_changed": sorted(p for p in set(old_map) & set(new_map) if old_map[p] != new_map[p]),
        "unmapped_added": sorted(set(new["unmapped"]) - set(old["unmapped"])),
        "unmapped_removed": sorted(set(old["unmapped"]) - set(new["unmapped"])),
    }
    return change if any(v for k, v in change.items() if k != "id") else None


def semantic_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_t = {t["id"]: t for t in old.get("templates", [])}
    new_t = {t["id"]: t for t in new.get("templates", [])}
    changed = [
        c
        for tid in new_t
        if tid in old_t and (c := _template_change(old_t[tid], new_t[tid])) is not None
    ]
    return {
        "templates_added": [tid for tid in new_t if tid not in old_t],
        "templates_removed": [tid for tid in old_t if tid not in new_t],
        "templates_changed": changed,
        "order_changed": [t for t in new_t if t in old_t] != [t for t in old_t if t in new_t],
        "changed_sections": [key for key in _TOP if old.get(key) != new.get(key)],
    }


def yaml_diff(old: str, new: str, old_label: str, new_label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=old_label,
            tofile=new_label,
        )
    )
