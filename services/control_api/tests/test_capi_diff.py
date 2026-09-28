"""Contract diff (C2): semantic over the compiled versions, unified over the YAML."""

from __future__ import annotations

from capi_helpers import submit, version
from control_api.diff import semantic_diff

from veyra_contracts import compile


def test_v1_to_v2_adds_the_failed_login_template(client, authsrv_source) -> None:
    submit(client, version(1))
    submit(client, version(2))
    body = client.get("/contracts/authsrv/diff", params={"from": 1, "to": 2}).json()
    assert body["semantic"]["templates_added"] == ["auth_failed"]
    assert body["semantic"]["templates_changed"] == []
    assert body["yaml"].startswith("--- authsrv@1\n+++ authsrv@2\n")
    assert "+  - id: auth_failed\n" in body["yaml"]


def test_defaults_compare_the_latest_with_the_one_before(client, authsrv_source) -> None:
    submit(client, version(1))
    submit(client, version(2))
    body = client.get("/contracts/authsrv/diff").json()
    assert (body["from_version"], body["to_version"]) == (1, 2)


def test_an_unknown_version_is_404(client, authsrv_source) -> None:
    submit(client, version(1))
    assert client.get("/contracts/authsrv/diff", params={"from": 1, "to": 7}).status_code == 404
    assert client.get("/contracts/authsrv/diff").status_code == 404  # no v0 before v1


def test_map_and_pattern_changes_are_named_per_template() -> None:
    old = compile(version(1)).to_dict()
    edited = (
        version(1)
        .replace("dst_endpoint.ip: $dst_ip", "device.ip: $dst_ip")
        .replace("OK login", "OK  login")
    )
    new = compile(edited.replace("status_id: {const: 1}", "status_id: {const: 99}")).to_dict()
    [change] = semantic_diff(old, new)["templates_changed"]
    assert change["map_added"] == ["device.ip"]
    assert change["map_removed"] == ["dst_endpoint.ip"]
    assert change["map_changed"] == ["status_id"]
    assert change["pattern_changed"] is False  # whitespace runs compile to the same regex


def test_formatting_only_changes_are_not_semantic() -> None:
    old = compile(version(1)).to_dict()
    new = compile("# a comment\n" + version(1)).to_dict()
    diff = semantic_diff(old, new)
    assert diff["templates_changed"] == [] and diff["changed_sections"] == []
