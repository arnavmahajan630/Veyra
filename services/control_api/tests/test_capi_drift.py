"""The drift inbox (C3): upsert from the worker, scoping, dismiss, auto-resolution, reset."""

from __future__ import annotations

from capi_helpers import T3_SIG, activate
from control_api.tables import AuditRow
from sqlmodel import Session as DbSession
from sqlmodel import select

T3 = [
    f"user={u} FAILED login from {ip} via 10.2.3.4 attempts:{n}"
    for u, ip, n in [
        ("a.sharma", "103.21.4.77", 1),
        ("a.sharma", "103.21.4.77", 2),
        ("r.patil", "10.4.9.2", 1),
        ("neel.k", "10.4.9.2", 1),
        ("s.iyer", "10.4.9.2", 1),
    ]
]


def upsert(client, count: int = 8, source: str = "src_authsrv_01", **extra) -> dict:  # type: ignore[no-untyped-def]
    body = {
        "source_id": source,
        "template_sig": T3_SIG,
        "drain_template": "user=<*> FAILED login from <*> via <*> attempts:<*>",
        "count": count,
        "samples_masked": T3,
        "sample_event_uids": [f"e{i}" for i in range(5)],
        "related_sigs": [],
        "first_seen": "2026-09-26T14:05:11Z",
        "last_seen": "2026-09-26T14:05:19Z",
        **extra,
    }
    response = client.post("/internal/drift", json=body)
    assert response.status_code == 200, response.text
    result: dict = response.json()
    return result


def test_the_first_upsert_creates_an_open_item(client, authsrv_source) -> None:
    """C3 AC1 (control-api half): one item, count 8, five distinct samples."""
    created = upsert(client)
    assert (created["state"], created["created"]) == ("open", True)
    [item] = client.get("/drift", params={"state": "open"}).json()
    assert (item["source_id"], item["template_sig"], item["count"]) == (
        "src_authsrv_01", T3_SIG, 8,
    )  # fmt: skip
    assert item["tenant_id"] == "t_maha_power" and len(set(item["samples_masked"])) == 5
    assert item["sample_event_uids"] == ["e0", "e1", "e2", "e3", "e4"]


def test_later_upserts_update_the_same_item(client, authsrv_source) -> None:
    first = upsert(client, count=5)
    second = upsert(client, count=9, last_seen="2026-09-26T14:06:00Z")
    assert first["drift_id"] == second["drift_id"] and second["created"] is False
    item = client.get(f"/drift/{first['drift_id']}").json()
    assert (item["count"], item["last_seen"]) == (9, "2026-09-26T14:06:00Z")


def test_an_unknown_source_is_404(client) -> None:
    body = {"source_id": "src_ghost", "template_sig": T3_SIG, "count": 1,
            "first_seen": "x", "last_seen": "x"}  # fmt: skip
    assert client.post("/internal/drift", json=body).status_code == 404


def test_other_tenants_do_not_see_the_item(client, authsrv_source, as_user) -> None:
    drift_id = upsert(client)["drift_id"]
    as_user("admin@veyra")
    assert len(client.get("/drift").json()) == 1
    client.post("/auth/logout")
    as_user("approver@veyra")
    assert client.get(f"/drift/{drift_id}").status_code == 200


def test_dismiss_is_audited_and_survives_later_upserts(client, authsrv_source, ctx) -> None:
    drift_id = upsert(client)["drift_id"]
    response = client.post(f"/drift/{drift_id}/dismiss")
    assert response.status_code == 200 and response.json()["state"] == "dismissed"
    upsert(client, count=20)
    assert client.get(f"/drift/{drift_id}").json()["state"] == "dismissed"
    with DbSession(ctx.engine) as db:
        actions = [r.action for r in db.exec(select(AuditRow)).all()]
    assert "drift.dismiss" in actions


def test_an_upsert_already_covered_by_the_active_version_is_resolved(
    client, authsrv_source, as_user
) -> None:
    activate(client, as_user, 1)
    activate(client, as_user, 2)
    assert upsert(client)["state"] == "resolved"


def test_promoting_a_covering_version_resolves_the_item(client, authsrv_source, as_user) -> None:
    """C3 AC2: after authsrv@2 is promoted, the item auto-resolves."""
    activate(client, as_user, 1)
    drift_id = upsert(client)["drift_id"]
    assert client.get(f"/drift/{drift_id}").json()["state"] == "open"  # v1 has no FAILED shape
    activate(client, as_user, 2)
    item = client.get(f"/drift/{drift_id}").json()
    assert (item["state"], item["resolved_by"]) == ("resolved", "authsrv@2")


def test_reset_tells_the_drift_worker(client, ctx) -> None:
    calls: list[str] = []
    ctx.drift_reset = lambda: calls.append("reset")
    assert client.post("/internal/reset", json={"scenario": "sih_main"}).status_code == 200
    assert calls == ["reset"]


def test_a_drift_worker_outage_does_not_fail_the_reset(client, ctx) -> None:
    def down() -> None:
        raise ConnectionError("drift-worker unreachable")

    ctx.drift_reset = down
    assert client.post("/internal/reset", json={"scenario": "sih_main"}).status_code == 200
