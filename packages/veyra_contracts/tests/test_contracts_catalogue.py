"""The catalogue must equal IF-OCSF-SUBSET in docs/plan/02_CONTRACTS.md."""

from __future__ import annotations

import json
from pathlib import Path

from veyra_common.topics import CATEGORIES
from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS

_SUBSET = (
    Path(__file__).resolve().parents[2]
    / "veyra_engine"
    / "src"
    / "veyra_engine"
    / "ocsf"
    / "subset_1.9.0.json"
)
# Present on the event schema, not offered to the drafter as mappable fields.
_NOT_MAPPED = frozenset(
    {
        "activity_id",
        "category_uid",
        "class_uid",
        "type_uid",
        "device.port",
        "enrichments",
        "metadata.version",
        "observables",
        "raw_data",
        "ulpf",
        "unmapped",
    }
)


def test_classes_match_if_ocsf_subset() -> None:
    assert {name: (c.class_uid, c.category) for name, c in CLASSES.items()} == {
        "base_event": (0, "uncategorized"),
        "process_activity": (1007, "system"),
        "authentication": (3002, "iam"),
        "network_activity": (4001, "network"),
        "http_activity": (4002, "network"),
    }


def test_activities_match_if_ocsf_subset() -> None:
    assert CLASSES["base_event"].activities == {"unknown": 0, "other": 99}
    assert CLASSES["process_activity"].activities == {"launch": 1, "terminate": 2}
    assert CLASSES["authentication"].activities == {"logon": 1, "logoff": 2}
    assert CLASSES["network_activity"].activities == {
        "open": 1,
        "close": 2,
        "refuse": 5,
        "traffic": 6,
    }
    assert CLASSES["http_activity"].activities["connect"] == 1
    assert CLASSES["http_activity"].activities["other"] == 99


def test_every_category_is_a_norm_topic() -> None:
    assert all(c.category in CATEGORIES for c in CLASSES.values())


def test_field_catalogue_matches_if_ocsf_subset() -> None:
    assert {
        "time", "message", "severity_id", "status_id", "status_detail", "disposition_id",
        "action_id", "metadata.product.name", "metadata.product.vendor_name",
        "src_endpoint.ip", "src_endpoint.port", "src_endpoint.hostname",
        "dst_endpoint.ip", "dst_endpoint.port", "dst_endpoint.hostname",
        "device.hostname", "device.ip",
        "user.name", "user.uid", "user.domain", "actor.user.name",
        "connection_info.protocol_name", "traffic.bytes_in", "traffic.bytes_out",
        "http_request.url.path", "http_request.http_method", "http_response.code",
        "process.name", "process.pid", "process.cmd_line",
    } == FIELDS  # fmt: skip
    assert "raw_data" not in FIELDS  # always present, never mapped


def _schema_leaves(node: object, prefix: str = "") -> set[str]:
    props = node.get("properties") if isinstance(node, dict) else None
    if not isinstance(props, dict):
        return set()
    found: set[str] = set()
    for name, child in props.items():
        path = f"{prefix}.{name}" if prefix else name
        if isinstance(child, dict) and isinstance(child.get("properties"), dict):
            found |= _schema_leaves(child, path)
        else:
            found.add(path)
    return found


def test_drafter_fields_match_the_vendored_subset() -> None:
    """A3's action: catalogue.FIELDS stays in step with subset_1.9.0.json."""
    document = json.loads(_SUBSET.read_text(encoding="utf-8"))
    leaves: set[str] = set()
    for schema in document["classes"].values():
        leaves |= _schema_leaves(schema)
    assert leaves - _NOT_MAPPED == FIELDS


def test_enums_match_if_ocsf_subset() -> None:
    assert set(ENUMS["severity_id"]) == {0, 1, 2, 3, 4, 5, 6}
    assert ENUMS["status_id"] == {0: "Unknown", 1: "Success", 2: "Failure", 99: "Other"}
    assert set(ENUMS["disposition_id"]) == {1, 2}
