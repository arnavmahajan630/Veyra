"""Drafts (C4): draft a drift item, review and edit it, submit it under four-eyes."""

from __future__ import annotations

from capi_helpers import T3_SIG, activate, t3_events
from control_api.security import hash_password
from control_api.tables import User
from sqlmodel import Session as DbSession

T3_TEXTS = [
    "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1",
    "user=r.patil FAILED login from 10.4.9.2 via 10.2.3.4 attempts:1",
]


def stored_t3(index, raw_store) -> None:  # type: ignore[no-untyped-def]
    for ref, envelope in t3_events(8):
        index.events.setdefault(T3_SIG, []).append(ref)
        raw_store.by_uid[ref.event_uid] = envelope


def drift_item(client) -> str:  # type: ignore[no-untyped-def]
    body = {
        "source_id": "src_authsrv_01",
        "template_sig": T3_SIG,
        "count": 8,
        "drain_template": "user=<*> FAILED login from <*> via <*> attempts:<*>",
        "samples_masked": T3_TEXTS,
        "first_seen": "x",
        "last_seen": "x",
    }
    drift_id: str = client.post("/internal/drift", json=body).json()["drift_id"]
    return drift_id


def drafted(client, as_user, index, raw_store) -> dict:  # type: ignore[no-untyped-def]
    stored_t3(index, raw_store)
    activate(client, as_user, 1)
    as_user("author@maha")
    drift_id = drift_item(client)
    response = client.post(f"/drift/{drift_id}/draft", json={})
    assert response.status_code == 202, response.text
    draft: dict = client.get(f"/drafts/{response.json()['draft_id']}").json()
    return draft


def test_a_drift_draft_is_ready_with_the_ac1_mapping(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    """C4 AC1: mapping, provenance 100 %, backtest 8/8 → tier 1 (heuristic mode in tests)."""
    draft = drafted(client, as_user, index, raw_store)
    assert (draft["state"], draft["contract_id"]) == ("ready", "authsrv")
    [template] = draft["templates"]
    assert template["source"] == "heuristic"
    assert template["template"]["pattern"] == (
        "user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>"
    )
    assert draft["verification"]["ok"] and all(r["ok"] for r in draft["verification"]["provenance"])
    assert (draft["backtest"]["n"], draft["backtest"]["tier_after"]) == (8, {"1": 8})
    assert "version: 2" in draft["yaml"]
    item = client.get(f"/drift/{draft['drift_id']}").json()
    assert (item["state"], item["draft_id"]) == ("draft_ready", draft["draft_id"])


def test_without_raw_events_the_draft_uses_masked_text(
    client, authsrv_source, as_user, index
) -> None:
    index.down = True
    activate(client, as_user, 1)
    as_user("author@maha")
    drift_id = drift_item(client)
    draft_id = client.post(f"/drift/{drift_id}/draft", json={}).json()["draft_id"]
    draft = client.get(f"/drafts/{draft_id}").json()
    assert draft["state"] == "ready" and draft["detail"].startswith("no raw events")


def test_patch_re_verifies_and_a_wrong_kind_is_a_type_issue(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    draft = drafted(client, as_user, index, raw_store)
    tokens = {t["value"]: t["id"] for t in draft["templates"][0]["request"]["tokens"]}
    edit = {
        "mappings": [
            {"ocsf_path": "src_endpoint.ip", "token": tokens["<USER_1>"]},
            {"ocsf_path": "status_id", "const": 2},
        ]
    }
    patched = client.patch(f"/drafts/{draft['draft_id']}", json=edit).json()
    assert any("is not an IP address" in i for i in patched["verification"]["type_issues"])
    assert patched["templates"][0]["source"] == "human"


def test_a_wrong_but_real_edit_keeps_provenance(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    """C4 AC3: src ↔ dst swapped: provenance ✓, no type issue; only review catches it."""
    draft = drafted(client, as_user, index, raw_store)
    tokens = {t["value"]: t["id"] for t in draft["templates"][0]["request"]["tokens"]}
    edit = {
        "mappings": [
            {"ocsf_path": "user.name", "token": tokens["<USER_1>"]},
            {"ocsf_path": "src_endpoint.ip", "token": tokens["10.2.3.4"]},
            {"ocsf_path": "dst_endpoint.ip", "token": tokens["103.21.4.77"]},
            {"ocsf_path": "status_id", "const": 2},
        ]
    }
    patched = client.patch(f"/drafts/{draft['draft_id']}", json=edit).json()
    assert patched["verification"]["ok"] and patched["verification"]["type_issues"] == []


def test_an_edit_outside_the_vocabulary_is_422(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    draft = drafted(client, as_user, index, raw_store)
    edit = {"mappings": [{"ocsf_path": "status_id", "const": 7}]}
    assert client.patch(f"/drafts/{draft['draft_id']}", json=edit).status_code == 422


def test_submit_makes_a_canary_and_the_submitter_is_the_author(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    draft = drafted(client, as_user, index, raw_store)
    response = client.post(f"/drafts/{draft['draft_id']}/submit")
    assert response.status_code == 201, response.text
    version = response.json()
    assert (version["state"], version["version"], version["author"]) == ("canary", 2, "author@maha")
    assert version["draft_id"] == draft["draft_id"]
    assert client.get(f"/drafts/{draft['draft_id']}").json()["state"] == "submitted"
    assert client.post(f"/drafts/{draft['draft_id']}/submit").status_code == 409


def test_autodraft_starts_a_draft_when_an_item_is_created(
    client, authsrv_source, as_user, ctx, index, raw_store
) -> None:
    stored_t3(index, raw_store)
    activate(client, as_user, 1)
    ctx.cfg.drift_autodraft = True
    drift_id = drift_item(client)
    item = client.get(f"/drift/{drift_id}").json()
    assert item["state"] == "draft_ready" and item["draft_id"]


def test_other_tenants_cannot_read_a_draft(
    client, authsrv_source, as_user, index, raw_store, ctx
) -> None:
    draft = drafted(client, as_user, index, raw_store)
    with DbSession(ctx.engine) as db:
        db.add(
            User(
                email="viewer@ntro",
                name="V",
                password_hash=hash_password("veyra-demo"),
                role="org_viewer",
                tenant_id="t_ntro_core",
            )
        )
        db.commit()
    as_user("viewer@ntro")
    assert client.get(f"/drafts/{draft['draft_id']}").status_code == 404
