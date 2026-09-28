"""The two endpoints the console shell needs (decision TC15): demo user switch, key list."""

from __future__ import annotations

from control_api.tables import AuditRow
from sqlmodel import Session as DbSession
from sqlmodel import select

NEW = {
    "id": "src_authsrv_01",
    "tenant_id": "t_maha_power",
    "name": "Auth Server",
    "vendor": "custom",
    "zone": "dmz",
    "transport": "http_push",
}


def test_demo_switch_becomes_the_other_user(client, login) -> None:
    login("author@maha")
    response = client.post("/auth/demo-switch", json={"email": "approver@veyra"})
    assert response.status_code == 200, response.text
    assert (response.json()["user"]["email"], response.json()["role"]) == (
        "approver@veyra",
        "pack_approver",
    )
    assert client.get("/auth/me").json()["user"]["email"] == "approver@veyra"


def test_demo_switch_ends_the_old_session(client, login) -> None:
    login("author@maha")
    old = client.cookies.get("veyra_session")
    client.post("/auth/demo-switch", json={"email": "approver@veyra"})
    client.cookies.set("veyra_session", old)
    assert client.get("/auth/me").status_code == 401


def test_demo_switch_is_audited(client, login, ctx) -> None:
    login("author@maha")
    client.post("/auth/demo-switch", json={"email": "approver@veyra"})
    with DbSession(ctx.engine) as db:
        rows = db.exec(select(AuditRow).where(AuditRow.action == "auth.demo_switch")).all()
    assert [(r.actor, r.target) for r in rows] == [("author@maha", "approver@veyra")]


def test_demo_switch_needs_a_session_demo_mode_and_a_real_user(client, login, ctx) -> None:
    assert client.post("/auth/demo-switch", json={"email": "admin@veyra"}).status_code == 401
    login("author@maha")
    assert client.post("/auth/demo-switch", json={"email": "ghost@veyra"}).status_code == 404
    ctx.cfg.demo_mode = False
    assert client.post("/auth/demo-switch", json={"email": "admin@veyra"}).status_code == 404


def test_key_list_shows_issued_and_revoked_keys_without_secrets(client, login) -> None:
    login("author@maha")
    client.post("/sources", json=NEW)
    first = client.post("/sources/src_authsrv_01/keys", json={}).json()["key_id"]
    second = client.post("/sources/src_authsrv_01/keys", json={}).json()["key_id"]
    client.post(f"/keys/{first}/revoke")

    rows = client.get("/sources/src_authsrv_01/keys").json()
    assert {r["key_id"]: r["status"] for r in rows} == {first: "revoked", second: "active"}
    assert set(rows[0]) == {
        "key_id", "source_id", "status", "quota_eps", "created_by", "created_at", "revoked_at",
    }  # fmt: skip


def test_key_list_is_scoped_to_the_tenant(client, login) -> None:
    login("author@maha")
    assert client.get("/sources/src_fw_dmz_01/keys").status_code == 404
