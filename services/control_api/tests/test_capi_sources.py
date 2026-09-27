"""Tenants, sources and keys: scoping (C1 AC3), publishing within the request, audit."""

from __future__ import annotations

from control_api.keys import IssuedKey, secret_digest
from control_api.security import hash_password
from control_api.tables import AuditRow, User
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


def add_user(ctx, email: str, role: str, tenant_id: str) -> None:
    with DbSession(ctx.engine) as db:
        db.add(
            User(
                email=email, name=email, password_hash=hash_password("veyra-demo"), role=role,
                tenant_id=tenant_id,
            )
        )  # fmt: skip
        db.commit()


def audit_actions(ctx) -> list[str]:
    with DbSession(ctx.engine) as db:
        return [row.action for row in db.exec(select(AuditRow)).all()]


def test_author_creates_a_source_in_own_tenant_and_it_is_published(client, login, producer) -> None:
    login("author@maha")
    response = client.post("/sources", json=NEW)
    assert response.status_code == 201, response.text
    message = producer.latest("control")["source:src_authsrv_01"]
    assert (message["tenant_id"], message["transport"]) == ("t_maha_power", "http_hec_event")


def test_author_cannot_create_in_another_tenant(client, login) -> None:
    login("author@maha")
    response = client.post("/sources", json={**NEW, "id": "src_x", "tenant_id": "t_ntro_core"})
    assert response.status_code == 404


def test_duplicate_source_is_a_conflict(client, login) -> None:
    login("admin@veyra")
    assert client.post("/sources", json=NEW).status_code == 201
    assert client.post("/sources", json=NEW).status_code == 409


def test_duplicate_tenant_is_a_conflict(client, login) -> None:
    login("admin@veyra")
    assert client.post("/tenants", json={"id": "t_ntro_core", "name": "x"}).status_code == 409


def test_syslog_sources_need_a_listener_and_match(client, login) -> None:
    login("admin@veyra")
    response = client.post("/sources", json={**NEW, "transport": "syslog_udp"})
    assert response.status_code == 422


def test_creating_a_syslog_source_rewrites_the_inventory(client, login, cfg) -> None:
    login("admin@veyra")
    body = {
        **NEW, "transport": "syslog_tcp", "listener": "dmz-tcp", "match_kind": "syslog_host",
        "match_value": "fw01",
    }  # fmt: skip
    assert client.post("/sources", json=body).status_code == 201
    rows = cfg.inventory_file.read_text(encoding="utf-8").splitlines()
    assert "dmz-tcp,syslog_host,fw01,src_authsrv_01,t_maha_power,custom,dmz" in rows


def test_a_broker_failure_returns_503_and_changes_nothing(client, login, producer, ctx) -> None:
    login("admin@veyra")
    producer.fail_with = "broker down"
    response = client.post("/sources", json=NEW)
    assert response.status_code == 503
    assert "broker down" in response.json()["detail"]
    producer.fail_with = None
    assert client.get("/sources/src_authsrv_01").status_code == 404
    assert "source.create" not in audit_actions(ctx)


def test_org_viewer_gets_404_for_another_tenants_resources(client, login, ctx) -> None:
    add_user(ctx, "viewer@maha", "org_viewer", "t_maha_power")
    login("viewer@maha")
    assert client.get("/sources/src_fw_dmz_01").status_code == 404
    assert client.get("/tenants/t_ntro_core").status_code == 404
    assert client.get("/sources").json() == []
    assert [t["id"] for t in client.get("/tenants").json()] == ["t_maha_power"]
    login("approver@veyra")
    assert {s["id"] for s in client.get("/sources").json()} == {"src_fw_dmz_01", "src_lnx_core_07"}


def test_org_viewer_cannot_create_sources(client, login, ctx) -> None:
    add_user(ctx, "viewer@maha", "org_viewer", "t_maha_power")
    login("viewer@maha")
    assert client.post("/sources", json=NEW).status_code == 403


def test_issue_key_returns_the_secret_once_and_publishes_only_its_digest(
    client, login, producer, ctx
) -> None:
    login("admin@veyra")
    client.post("/sources", json=NEW)
    response = client.post("/sources/src_authsrv_01/keys", json={})
    assert response.status_code == 201, response.text
    card = response.json()
    secret = card["secret"]
    assert secret.startswith("veyra_") and len(secret) == 38
    assert card["endpoints"]["hec_url"] == "http://localhost:8088/services/collector/event"
    message = producer.latest("control")[f"apikey:{card['key_id']}"]
    assert message["secret_sha256"] == secret_digest(ctx.pepper, secret)
    assert (message["status"], message["quota_eps"]) == (
        "active",
        ctx.cfg.gateway_default_quota_eps,
    )
    assert all(secret not in str(value) for _, _, value in producer.messages if value)
    assert ctx.last_key.get() == IssuedKey(card["key_id"], secret, "src_authsrv_01")


def test_revoke_publishes_status_revoked(client, login, producer) -> None:
    login("admin@veyra")
    client.post("/sources", json=NEW)
    key_id = client.post("/sources/src_authsrv_01/keys", json={}).json()["key_id"]
    response = client.post(f"/keys/{key_id}/revoke")
    assert response.json() == {"key_id": key_id, "status": "revoked"}
    assert producer.latest("control")[f"apikey:{key_id}"]["status"] == "revoked"


def test_revoking_another_tenants_key_is_404(client, login) -> None:
    login("admin@veyra")
    key_id = client.post("/sources/src_fw_dmz_01/keys", json={}).json()["key_id"]
    login("author@maha")
    assert client.post(f"/keys/{key_id}/revoke").status_code == 404


def test_patch_source_republishes_it(client, login, producer) -> None:
    login("admin@veyra")
    client.post("/sources", json=NEW)
    response = client.patch("/sources/src_authsrv_01", json={"status": "paused"})
    assert response.status_code == 200 and response.json()["status"] == "paused"
    assert producer.latest("control")["source:src_authsrv_01"]["status"] == "paused"


def test_every_mutation_is_audited(client, login, ctx) -> None:
    login("admin@veyra")
    client.post("/tenants", json={"id": "t_extra", "name": "Extra"})
    client.patch("/tenants/t_extra", json={"name": "Extra Ltd"})
    client.post("/sources", json=NEW)
    client.patch("/sources/src_authsrv_01", json={"expected_eps": 5})
    key_id = client.post("/sources/src_authsrv_01/keys", json={}).json()["key_id"]
    client.post(f"/keys/{key_id}/revoke")
    assert {
        "tenant.create", "tenant.update", "source.create", "source.update", "key.create",
        "key.revoke",
    } <= set(audit_actions(ctx))  # fmt: skip
