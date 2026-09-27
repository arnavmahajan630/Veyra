"""The catalogue must equal IF-OCSF-SUBSET in docs/plan/02_CONTRACTS.md."""

from __future__ import annotations

from veyra_common.topics import CATEGORIES
from veyra_contracts.catalogue import CLASSES, ENUMS, FIELDS


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


def test_enums_match_if_ocsf_subset() -> None:
    assert set(ENUMS["severity_id"]) == {0, 1, 2, 3, 4, 5, 6}
    assert ENUMS["status_id"] == {0: "Unknown", 1: "Success", 2: "Failure", 99: "Other"}
    assert set(ENUMS["disposition_id"]) == {1, 2}
