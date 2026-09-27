"""Sessions and /auth, plus first boot (republish_all + inventory before serving)."""

from __future__ import annotations

from control_api.context import first_boot
from control_api.tables import AuditRow, Source
from sqlmodel import Session as DbSession
from sqlmodel import select


def test_login_sets_an_httponly_strict_session_cookie(client) -> None:
    response = client.post("/auth/login", json={"email": "author@maha", "password": "veyra-demo"})
    assert response.status_code == 200
    assert response.json() == {
        "user": {"email": "author@maha", "name": "Maha author"},
        "role": "pack_author",
        "tenant": "t_maha_power",
        "demo_mode": True,
    }
    cookie = response.headers["set-cookie"].lower()
    assert "veyra_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie


def test_me_requires_a_session(client) -> None:
    assert client.get("/auth/me").status_code == 401


def test_me_returns_the_signed_in_user(client, login) -> None:
    login("approver@veyra")
    me = client.get("/auth/me").json()
    assert (me["role"], me["tenant"]) == ("pack_approver", "*")


def test_wrong_password_and_unknown_email_get_the_same_401(client) -> None:
    wrong = client.post("/auth/login", json={"email": "author@maha", "password": "nope"})
    unknown = client.post("/auth/login", json={"email": "ghost@veyra", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_an_expired_session_is_rejected(client, login, clock, cfg) -> None:
    login("author@maha")
    clock.advance(cfg.session_ttl_min * 60 + 1)
    assert client.get("/auth/me").status_code == 401


def test_logout_ends_the_session(client, login) -> None:
    login("author@maha")
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_login_is_audited(client, login, ctx) -> None:
    login("author@maha")
    with DbSession(ctx.engine) as db:
        rows = db.exec(select(AuditRow).where(AuditRow.action == "auth.login")).all()
    assert [(r.actor, r.tenant_id) for r in rows] == [("author@maha", "t_maha_power")]


def test_first_boot_publishes_control_and_writes_the_inventory(ctx, producer, cfg) -> None:
    latest = producer.latest("control")
    assert {
        "source:src_fw_dmz_01", "source:src_lnx_core_07", "contract:linux_sshd",
        "contract:acme_ngfw_cef", "vocab:status_words", "routes",
    } <= set(latest)  # fmt: skip
    assert latest["contract:linux_sshd"]["compiled"]["contract"] == "linux_sshd"
    assert cfg.inventory_file.read_text(encoding="utf-8").splitlines()[1:] == [
        "core-udp,syslog_host,core-lnx-07,src_lnx_core_07,t_ntro_core,linux,core",
        "dmz-tcp,syslog_host,fw-dmz-01,src_fw_dmz_01,t_ntro_core,acme_ngfw,dmz",
    ]


def test_first_boot_keeps_an_existing_database(ctx) -> None:
    with DbSession(ctx.engine) as db:
        db.add(
            Source(
                id="src_kept", tenant_id="t_maha_power", name="k", vendor="custom", zone="dmz",
                transport="http_push", created_at="x",
            )
        )  # fmt: skip
        db.commit()
    first_boot(ctx)
    with DbSession(ctx.engine) as db:
        assert db.get(Source, "src_kept") is not None
