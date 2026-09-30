"""Event shapes and the fake producer the router's tests share.

A module of its own rather than `conftest.py`, because two service test suites with a `conftest`
each cannot both be imported by name — the gateway's tests hit the same wall.

The events are the four tiers as the engine really emits them (A3 to A5): every filter, format and
rule decision reads `ulpf`, the one part of the event that is present even when nothing parsed.
"""

from __future__ import annotations

import json
from typing import Any

RECEIVED = "2026-09-26T14:10:00.000000000Z"


class FakeProducer:
    """Records what was produced to `receipts`."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str | None, bytes]] = []

    def produce(self, topic: str, value: bytes | None = None, key: Any = None, **_: Any) -> None:
        self.messages.append((topic, key, value or b""))

    def flush(self, timeout: float = 10) -> int:
        return 0

    def poll(self, timeout: float = 0) -> int:
        return 0

    def receipts(self) -> list[dict[str, Any]]:
        return [json.loads(value) for topic, _, value in self.messages if topic == "receipts"]


def norm_event(
    *,
    tier: int = 1,
    class_uid: int = 3002,
    tenant: str = "t_maha_power",
    source: str = "src_authsrv_01",
    revision: int = 1,
    user: str = "a.sharma",
    src_ip: str = "103.21.4.77",
    event_uid: str = "0192a4f0-0000-7000-8000-000000000001",
) -> dict[str, Any]:
    """One IF-NORM-EVENT, in the shape the engine produces for the given tier."""
    event: dict[str, Any] = {
        "class_uid": class_uid,
        "category_uid": 3 if class_uid == 3002 else 0,
        "type_uid": class_uid * 100 + 1 if class_uid else 99,
        "activity_id": 1 if class_uid else 99,
        "severity_id": 3,
        "status_id": 2,
        "time": 1790000000000,
        "message": f"user={user} FAILED login from {src_ip}",
        "raw_data": f"<134>Sep 26 14:05:11 fw01 app[233]: user={user} FAILED login from {src_ip}",
        "observables": [{"name": "user", "type_id": 4, "value": user}],
        "unmapped": {},
        "metadata": {"version": "1.9.0", "product": {"name": "VEYRA", "vendor_name": "NTRO"}},
        "ulpf": {
            "v": 1,
            "event_uid": event_uid,
            "tenant_id": tenant,
            "source_id": source,
            "vendor": "custom",
            "zone": "dmz",
            "tier": tier,
            "conformance": "match" if tier == 1 else "unknown_template",
            "contract": {"id": "authsrv", "version": 2} if tier == 1 else None,
            "template": {"sig": "t_abcdef123456", "id": "auth_failed" if tier == 1 else None},
            "revision": revision,
            "supersedes": None if revision == 1 else f"{event_uid}@{revision - 1}",
            "replay": revision > 1,
            "received_time": RECEIVED,
            "raw_ref": {"topic": "raw.custom", "partition": 0, "offset": 17},
        },
    }
    if tier == 1:
        event["user"] = {"name": user}
        event["src_endpoint"] = {"ip": src_ip}
    else:
        event["class_uid"] = 0
        event["category_uid"] = 0
        event["observables"] = [
            {"name": "user", "type_id": 4, "value": user},
            {"name": "ip_1", "type_id": 2, "value": src_ip},
        ]
    return event
