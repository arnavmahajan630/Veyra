"""IF-OCSF-SUBSET: the classes, activities, fields and enums a Log Contract may use.

Hand-written from docs/plan/02_CONTRACTS.md (OCSF 1.9.0) until A3 vendors the schema
into packages/veyra_engine/ocsf/. Extend additively, exactly like the IF section.
"""

from __future__ import annotations

from dataclasses import dataclass

from veyra_common.topics import category_for_class


@dataclass(frozen=True, slots=True)
class OcsfClass:
    name: str
    class_uid: int
    activities: dict[str, int]

    @property
    def category(self) -> str:
        return category_for_class(self.class_uid)


CLASSES: dict[str, OcsfClass] = {
    c.name: c
    for c in (
        OcsfClass("base_event", 0, {"unknown": 0, "other": 99}),
        OcsfClass("process_activity", 1007, {"launch": 1, "terminate": 2}),
        OcsfClass("authentication", 3002, {"logon": 1, "logoff": 2}),
        OcsfClass("network_activity", 4001, {"open": 1, "close": 2, "refuse": 5, "traffic": 6}),
        OcsfClass(
            "http_activity",
            4002,
            {
                "connect": 1,
                "delete": 2,
                "get": 3,
                "head": 4,
                "options": 5,
                "post": 6,
                "put": 7,
                "trace": 8,
                "other": 99,
            },
        ),
    )
}

FIELDS: frozenset[str] = frozenset(
    {
        # common
        "time", "message", "severity_id", "status_id", "status_detail", "disposition_id",
        "action_id", "metadata.product.name", "metadata.product.vendor_name",
        # endpoints
        "src_endpoint.ip", "src_endpoint.port", "src_endpoint.hostname",
        "dst_endpoint.ip", "dst_endpoint.port", "dst_endpoint.hostname",
        "device.hostname", "device.ip",
        # identity
        "user.name", "user.uid", "user.domain", "actor.user.name",
        # network
        "connection_info.protocol_name", "traffic.bytes_in", "traffic.bytes_out",
        # http
        "http_request.url.path", "http_request.http_method", "http_response.code",
        # process
        "process.name", "process.pid", "process.cmd_line",
    }
)  # fmt: skip

ENUMS: dict[str, dict[int, str]] = {
    "severity_id": {
        0: "Unknown", 1: "Informational", 2: "Low", 3: "Medium",
        4: "High", 5: "Critical", 6: "Fatal",
    },
    "status_id": {0: "Unknown", 1: "Success", 2: "Failure", 99: "Other"},
    "disposition_id": {1: "Allowed", 2: "Blocked"},
}  # fmt: skip
