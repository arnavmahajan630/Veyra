"""Backend additions for the C6 pages: routes, clone a library pack, re-draft (TC41)."""

from __future__ import annotations

from capi_helpers import T3_SIG, activate


def test_routes_are_readable_by_any_signed_in_user(client, login) -> None:
    login("author@maha")
    body = client.get("/routes").json()
    assert {r["id"] for r in body["routes"]} >= {"wazuh_main"}


def test_use_library_clones_the_pack_as_version_one(client, authsrv_source) -> None:
    response = client.post(
        "/onboarding/use-library", json={"source_id": "src_authsrv_01", "pack": "generic_cef"}
    )
    assert response.status_code == 201, response.text
    version = response.json()
    assert (version["contract_id"], version["version"], version["state"]) == (
        "authsrv", 1, "canary",
    )  # fmt: skip
    assert "tenant: t_maha_power" in version["yaml"] and "drafted_by: library" in version["yaml"]


def test_an_unknown_pack_is_404(client, authsrv_source) -> None:
    body = {"source_id": "src_authsrv_01", "pack": "nope"}
    assert client.post("/onboarding/use-library", json=body).status_code == 404


def test_a_drafting_item_can_be_redrafted(client, authsrv_source, as_user, ctx) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    body = {"source_id": "src_authsrv_01", "template_sig": T3_SIG, "count": 8,
            "samples_masked": ["user=a FAILED login from 1.2.3.4 via 10.2.3.4 attempts:1"],
            "first_seen": "x", "last_seen": "x"}  # fmt: skip
    drift_id = client.post("/internal/drift", json=body).json()["drift_id"]
    ctx.spawn = lambda work: None  # leave the first draft "drafting"
    first = client.post(f"/drift/{drift_id}/draft", json={}).json()["draft_id"]
    second = client.post(f"/drift/{drift_id}/draft", json={"mode": "cache"})
    assert second.status_code == 202 and second.json()["draft_id"] != first
    assert client.get(f"/drift/{drift_id}").json()["draft_id"] == second.json()["draft_id"]


class _ModelDown:
    """A drafter whose model never answers (the live draft the 5 s fallback gave up on)."""

    def draft(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("model down")


def test_a_superseded_draft_finishing_late_leaves_the_item_alone(
    client, authsrv_source, as_user, ctx
) -> None:
    activate(client, as_user, 1)
    as_user("author@maha")
    body = {"source_id": "src_authsrv_01", "template_sig": T3_SIG, "count": 8,
            "samples_masked": ["user=a FAILED login from 1.2.3.4 via 10.2.3.4 attempts:1"],
            "first_seen": "x", "last_seen": "x"}  # fmt: skip
    drift_id = client.post("/internal/drift", json=body).json()["drift_id"]
    works: list = []
    ctx.spawn = works.append
    client.post(f"/drift/{drift_id}/draft", json={})
    current = client.post(f"/drift/{drift_id}/draft", json={"mode": "cache"}).json()["draft_id"]
    works[1]()  # the cache re-draft finishes first
    drafter, ctx.drafter = ctx.drafter, _ModelDown()
    works[0]()  # then the abandoned live draft fails
    ctx.drafter = drafter
    item = client.get(f"/drift/{drift_id}").json()
    assert (item["state"], item["draft_id"]) == ("draft_ready", current)
