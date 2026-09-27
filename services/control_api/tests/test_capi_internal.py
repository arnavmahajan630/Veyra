"""GET /audit (AC5), /stream auth, and the internal endpoints the demo engine calls (AC4)."""

from __future__ import annotations

NEW = {
    "id": "src_authsrv_01",
    "tenant_id": "t_maha_power",
    "name": "Auth Server",
    "vendor": "custom",
    "zone": "dmz",
    "transport": "http_push",
}

C1_PATHS = {
    "/auth/login", "/auth/logout", "/auth/me", "/tenants", "/tenants/{tenant_id}", "/sources",
    "/sources/{source_id}", "/sources/{source_id}/keys", "/keys/{key_id}/revoke", "/audit",
    "/stream", "/internal/drift", "/internal/reset", "/internal/demo/last-key",
}  # fmt: skip


def test_openapi_lists_every_c1_endpoint(client) -> None:
    assert set(client.get("/openapi.json").json()["paths"]) >= C1_PATHS


def test_audit_requires_a_session(client) -> None:
    assert client.get("/audit").status_code == 401


def test_audit_is_scoped_to_the_callers_tenant(client, login) -> None:
    login("admin@veyra")
    client.post("/sources", json=NEW)
    login("author@maha")
    rows = client.get("/audit").json()
    assert {"source.create", "auth.login"} <= {r["action"] for r in rows}
    assert not any(r["actor"] == "admin@veyra" and r["action"] == "auth.login" for r in rows)
    login("admin@veyra")
    everything = client.get("/audit").json()
    assert any(r["actor"] == "admin@veyra" and r["action"] == "auth.login" for r in everything)
    assert len(client.get("/audit", params={"limit": 1}).json()) == 1


def test_stream_requires_a_session(client) -> None:
    assert client.get("/stream").status_code == 401


def test_reset_returns_to_the_seed_quickly(client, login, ctx, producer, cfg) -> None:
    login("admin@veyra")
    client.post("/sources", json=NEW)
    client.post("/sources/src_authsrv_01/keys", json={})
    response = client.post("/internal/reset", json={"scenario": "sih_main"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["seconds"] < 10
    assert (body["tenants"], body["users"], body["sources"], body["contracts"]) == (2, 3, 2, 2)
    login("admin@veyra")  # reset wiped sessions
    assert [s["id"] for s in client.get("/sources").json()] == ["src_fw_dmz_01", "src_lnx_core_07"]
    assert ctx.last_key.get() is None
    assert len(cfg.inventory_file.read_text(encoding="utf-8").splitlines()) == 3
    republished = [
        k for t, k, _ in producer.messages if t == "control" and k == "source:src_fw_dmz_01"
    ]
    assert len(republished) >= 2  # first boot + reset


def test_reset_rejects_unknown_scenarios(client) -> None:
    assert client.post("/internal/reset", json={"scenario": "nope"}).status_code == 400


def test_last_key_is_served_only_in_demo_mode(client, login, ctx) -> None:
    assert client.get("/internal/demo/last-key").status_code == 404
    login("admin@veyra")
    client.post("/sources", json=NEW)
    card = client.post("/sources/src_authsrv_01/keys", json={}).json()
    assert client.get("/internal/demo/last-key").json() == {
        "key_id": card["key_id"],
        "secret": card["secret"],
        "source_id": "src_authsrv_01",
    }
    ctx.cfg = ctx.cfg.model_copy(update={"demo_mode": False})
    assert client.get("/internal/demo/last-key").status_code == 404


def test_internal_drift_is_accepted(client) -> None:
    assert client.post("/internal/drift", json={"source_id": "src_a"}).status_code == 202
