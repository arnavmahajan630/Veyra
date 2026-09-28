"""Contract lifecycle (C2): submit, four-eyes approve, promote, rollback, and what is published."""

from __future__ import annotations

from capi_helpers import SOURCE, activate, submit, version
from control_api.security import hash_password
from control_api.tables import AuditRow, User
from sqlmodel import Session as DbSession
from sqlmodel import select


def test_submit_reaches_canary_and_commits_to_the_registry(
    client, authsrv_source, ctx, producer
) -> None:
    body = submit(client, version(1))
    assert body["state"] == "canary"
    assert body["golden"]["passed"] and body["lint"] == []
    assert body["git_commit"] and len(body["git_commit"]) == 40
    assert (ctx.cfg.contracts_repo / "t_maha_power" / "authsrv.yaml").read_text() == version(1)
    # TC16: a contract with no active version is not on `control` yet.
    assert "contract:authsrv" not in producer.latest("control")


def test_the_author_cannot_approve_their_own_version(client, as_user, ctx) -> None:
    with DbSession(ctx.engine) as db:  # an admin may both submit and approve, so four-eyes bites
        db.add(User(email="ops@veyra", name="Ops", password_hash=hash_password("veyra-demo"),
                    role="admin", tenant_id="*"))  # fmt: skip
        db.commit()
    as_user("author@maha")
    client.post("/sources", json=SOURCE)
    as_user("ops@veyra")
    submit(client, version(1))
    response = client.post("/contracts/authsrv/versions/1/approve")
    assert response.status_code == 403
    assert "four-eyes" in response.json()["detail"]


def test_a_pack_author_cannot_approve_at_all(client, authsrv_source) -> None:
    submit(client, version(1))
    assert client.post("/contracts/authsrv/versions/1/approve").status_code == 403


def test_promote_needs_approval_first(client, authsrv_source, as_user) -> None:
    submit(client, version(1))
    as_user("approver@veyra")
    response = client.post("/contracts/authsrv/versions/1/promote")
    assert response.status_code == 409 and "approve" in response.json()["detail"]


def test_promote_publishes_the_active_contract_and_binds_the_source(
    client, authsrv_source, as_user, producer
) -> None:
    activate(client, as_user, 1)
    control = producer.latest("control")
    assert control["contract:authsrv"]["version"] == 1
    assert control["contract:authsrv"]["candidate"] is None
    assert control["contract:authsrv"]["compiled"]["templates"][0]["id"] == "auth_ok"
    assert control["source:src_authsrv_01"]["contract_id"] == "authsrv"


def test_a_canary_is_published_as_the_candidate_beside_the_active(
    client, authsrv_source, as_user, producer
) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    message = producer.latest("control")["contract:authsrv"]
    assert (message["version"], message["candidate"]["version"]) == (1, 2)


def test_promoting_v2_retires_v1(client, authsrv_source, as_user, producer) -> None:
    activate(client, as_user, 1)
    activate(client, as_user, 2)
    detail = client.get("/contracts/authsrv").json()
    states = {v["version"]: (v["state"], v["retired_reason"]) for v in detail["versions"]}
    assert states == {1: ("retired", "replaced by v2"), 2: ("active", None)}
    assert producer.latest("control")["contract:authsrv"]["version"] == 2


def test_rollback_restores_v1_and_is_audited(
    client, authsrv_source, as_user, producer, ctx
) -> None:
    activate(client, as_user, 1)
    activate(client, as_user, 2)
    response = client.post("/contracts/authsrv/rollback", json={"to_version": 1})
    assert response.status_code == 200, response.text
    assert producer.latest("control")["contract:authsrv"]["version"] == 1
    detail = client.get("/contracts/authsrv").json()
    assert (detail["active_version"], detail["versions"][1]["state"]) == (1, "retired")
    with DbSession(ctx.engine) as db:
        actions = [r.action for r in db.exec(select(AuditRow)).all()]
    assert "contract.rollback" in actions


def test_rollback_to_a_never_active_version_is_refused(client, authsrv_source, as_user) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    as_user("approver@veyra")
    response = client.post("/contracts/authsrv/rollback", json={"to_version": 2})
    assert response.status_code == 409


def test_a_new_submission_supersedes_the_canary(client, authsrv_source, as_user) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    submit(client, version(3))
    detail = client.get("/contracts/authsrv").json()
    assert detail["canary_version"] == 3
    assert detail["versions"][1]["retired_reason"] == "superseded by v3"


def test_a_compile_error_is_422_with_its_location(client, authsrv_source) -> None:
    response = client.post("/contracts", json={"yaml": version(1).replace("logon", "nap", 1)})
    assert response.status_code == 422
    assert response.json()["detail"]["line"] is not None


def test_the_version_number_must_be_the_next_one(client, authsrv_source) -> None:
    assert client.post("/contracts", json={"yaml": version(2)}).status_code == 409
    submit(client, version(1))
    assert client.post("/contracts", json={"yaml": version(1)}).status_code == 409
    assert client.post("/contracts", json={"yaml": version(3)}).status_code == 409


def test_a_lint_error_keeps_a_draft_that_can_be_resubmitted(
    client, authsrv_source, producer, ctx
) -> None:
    broken = version(1).replace("required: [time, user.name]", "required: [time, process.pid]")
    body = submit(client, broken)
    assert (body["state"], body["git_commit"]) == ("draft", None)
    assert body["lint"][0]["code"] == "required_unproduced"
    assert not (ctx.cfg.contracts_repo / "t_maha_power" / "authsrv.yaml").exists()

    fixed = submit(client, version(1))
    assert fixed["state"] == "canary"


def test_the_source_must_belong_to_the_tenant(client, as_user) -> None:
    as_user("author@maha")
    response = client.post("/contracts", json={"yaml": version(1)})  # src_authsrv_01 not created
    assert response.status_code == 422


def test_tenant_scoping(client, authsrv_source, as_user) -> None:
    submit(client, version(1))
    assert [c["id"] for c in client.get("/contracts").json()] == ["authsrv"]
    assert client.get("/contracts/linux_sshd").status_code == 404
    as_user("approver@veyra")
    ids = [c["id"] for c in client.get("/contracts").json()]
    assert ids == ["acme_ngfw_cef", "authsrv", "linux_sshd"]


def test_the_history_records_every_step_with_its_actor(client, authsrv_source, as_user) -> None:
    activate(client, as_user, 1)
    history = client.get("/contracts/authsrv").json()["history"]
    assert [(h["action"], h["to_state"], h["actor"]) for h in history] == [
        ("submitted", "draft", "author@maha"),
        ("tested", "testing", "author@maha"),
        ("canary", "canary", "author@maha"),
        ("approved", "canary", "approver@veyra"),
        ("promoted", "active", "approver@veyra"),
    ]


def test_version_detail_carries_yaml_compiled_and_reports(client, authsrv_source) -> None:
    submit(client, version(1))
    body = client.get("/contracts/authsrv/versions/1").json()
    assert body["yaml"] == version(1)
    assert body["compiled"]["contract"] == "authsrv"
    assert body["compiled"]["compiled_at"] == body["created_at"]
    assert client.get("/contracts/authsrv/versions/9").status_code == 404


def test_reset_tombstones_what_the_seed_does_not_have(
    client, authsrv_source, as_user, producer
) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    key_id = client.post("/sources/src_authsrv_01/keys", json={}).json()["key_id"]
    assert client.post("/internal/reset", json={"scenario": "sih_main"}).status_code == 200
    control = producer.latest("control")
    assert control["contract:authsrv"] is None
    assert control["source:src_authsrv_01"] is None
    assert control[f"apikey:{key_id}"] is None
    assert control["contract:linux_sshd"]["version"] == 1  # the seed is republished
