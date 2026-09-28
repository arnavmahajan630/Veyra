"""Authentication and the registry: who gets in, who gets 401, and how fast a revocation lands."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from gateway_helpers import (
    KEY_ID,
    PEPPER,
    SECRET,
    SOURCE_ID,
    TENANT,
    apikey_message,
    source_message,
)
from ingest_gateway.registry import AuthFailure, KeyRegistry
from ingest_gateway.settings import GatewaySettings

from veyra_common.hashing import sha256_hex


def test_the_digest_matches_control_apis_formula() -> None:
    """If these two ever disagree, every key silently stops working."""
    registry = KeyRegistry(PEPPER, cfg=GatewaySettings(_env_file=None))
    assert registry.digest(SECRET) == sha256_hex(PEPPER + SECRET.encode("utf-8"))


def test_a_valid_key_resolves_to_its_source(registry: KeyRegistry) -> None:
    principal = registry.resolve(SECRET)
    assert principal.key_id == KEY_ID
    assert principal.source_id == SOURCE_ID
    assert principal.tenant_id == TENANT
    assert principal.vendor == "custom"
    assert principal.zone == "dmz"


def test_an_unknown_secret_is_rejected(registry: KeyRegistry) -> None:
    with pytest.raises(AuthFailure, match="unknown key"):
        registry.resolve("not-the-secret")


def test_an_empty_credential_is_rejected(registry: KeyRegistry) -> None:
    with pytest.raises(AuthFailure, match="no credential"):
        registry.resolve("")


def test_a_wrong_pepper_makes_the_digest_miss(cfg: GatewaySettings) -> None:
    """The gateway and control-api must share the pepper file; a mismatch must fail closed."""
    registry = KeyRegistry(b"1" * 64, cfg=cfg)
    registry._handle(f"apikey:{KEY_ID}", apikey_message())
    with pytest.raises(AuthFailure, match="unknown key"):
        registry.resolve(SECRET)


def test_revocation_replaces_the_active_key(registry: KeyRegistry) -> None:
    """control-api republishes the same key with status=revoked; it must stop working at once."""
    registry._handle(f"apikey:{KEY_ID}", apikey_message(status="revoked"))
    with pytest.raises(AuthFailure, match="revoked"):
        registry.resolve(SECRET)


def test_a_tombstone_deletes_the_key(registry: KeyRegistry) -> None:
    registry._handle(f"apikey:{KEY_ID}", None)
    assert registry.key_count == 0
    with pytest.raises(AuthFailure, match="unknown key"):
        registry.resolve(SECRET)


def test_a_rotated_secret_does_not_leave_the_old_one_working(registry: KeyRegistry) -> None:
    """The dangerous case: re-issuing a key under the same key_id must invalidate the old digest."""
    new_secret = "rotated-secret"
    registry._handle(
        f"apikey:{KEY_ID}",
        apikey_message(secret_sha256=sha256_hex(PEPPER + new_secret.encode())),
    )
    assert registry.resolve(new_secret).key_id == KEY_ID
    with pytest.raises(AuthFailure, match="unknown key"):
        registry.resolve(SECRET)


def test_the_pepper_id_is_computed_c1s_way(cfg: GatewaySettings) -> None:
    registry = KeyRegistry(PEPPER, cfg=cfg)
    assert registry.pepper_id == "p_" + sha256_hex(PEPPER)[:8]


def test_a_key_from_another_pepper_is_named_as_such(registry: KeyRegistry) -> None:
    """The digest would miss anyway; the point is a log line that identifies the real fault."""
    registry._handle(f"apikey:{KEY_ID}", apikey_message(pepper_id="p_deadbeef"))
    with pytest.raises(AuthFailure, match="minted under pepper p_deadbeef"):
        registry.resolve(SECRET)


def test_a_paused_source_is_refused(registry: KeyRegistry) -> None:
    registry._handle(f"source:{SOURCE_ID}", source_message(status="paused"))
    with pytest.raises(AuthFailure, match="paused"):
        registry.resolve(SECRET)


def test_a_key_without_a_source_record_still_works(cfg: GatewaySettings) -> None:
    """Onboarding issues the key before the source row lands; losing the event would break P2."""
    registry = KeyRegistry(PEPPER, cfg=cfg)
    registry._handle(f"apikey:{KEY_ID}", apikey_message())
    principal = registry.resolve(SECRET)
    assert principal.source_id == SOURCE_ID
    assert principal.tenant_id == TENANT
    assert principal.vendor == cfg.default_vendor == "custom"
    assert principal.zone == cfg.default_zone == "dmz"


def test_the_quota_comes_from_the_key(registry: KeyRegistry) -> None:
    registry._handle(f"apikey:{KEY_ID}", apikey_message(quota_eps=7))
    assert registry.resolve(SECRET).quota_eps == 7


# ---------------------------------------------------------------- over HTTP
def test_splunk_and_bearer_schemes_both_work(client: TestClient) -> None:
    for header in (f"Splunk {SECRET}", f"Bearer {SECRET}", f"splunk {SECRET}"):
        response = client.post(
            "/services/collector/event",
            content='{"event":"line"}',
            headers={"Authorization": header},
        )
        assert response.status_code == 200, header


def test_the_token_query_parameter_works(client: TestClient) -> None:
    """Some HEC clients pass the token in the URL; refusing would look like a bad key."""
    response = client.post(f"/services/collector/event?token={SECRET}", content='{"event":"x"}')
    assert response.status_code == 200


def test_no_credential_is_a_hec_shaped_401(client: TestClient) -> None:
    response = client.post("/services/collector/event", content='{"event":"x"}')
    assert response.status_code == 401
    assert response.json() == {"text": "Invalid authorization", "code": 3}


def test_a_bad_key_never_says_why(client: TestClient) -> None:
    """The reason goes to the log, not to the client — it would be an oracle."""
    response = client.post(
        "/services/collector/event",
        content='{"event":"x"}',
        headers={"Authorization": "Splunk wrong"},
    )
    assert response.status_code == 401
    assert "wrong" not in response.text and "unknown" not in response.text


def test_requests_before_the_control_backlog_is_read_get_503(
    client: TestClient, registry: KeyRegistry, auth: dict[str, str]
) -> None:
    """503, not 401: we do not yet know whether the key is good, and 401 stops a client retrying."""
    registry.reader.ready.clear()
    response = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert response.status_code == 503
    assert response.json()["code"] == 9


def test_a_revoked_key_is_401_over_http(
    client: TestClient, registry: KeyRegistry, auth: dict[str, str], fake: Any
) -> None:
    registry._handle(f"apikey:{KEY_ID}", apikey_message(status="revoked"))
    response = client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert response.status_code == 401
    assert not fake.messages, "a revoked key must not produce anything"
