"""Constants and message builders shared by the gateway's tests.

The API-key and source payloads mirror `packages/veyra_common/fixtures/control_apikey.json` and
`control_source.json` — the messages control-api really publishes — so a change in those shapes
fails here rather than in production.
"""

from __future__ import annotations

from typing import Any

from veyra_common.hashing import sha256_hex

PEPPER = b"0" * 64
SECRET = "s3cr3t-token-value"
KEY_ID = "k_authsrv_01"
SOURCE_ID = "src_authsrv_01"
TENANT = "t_maha_power"
# C1 derives this from the pepper file; a key whose pepper_id does not match is refused.
PEPPER_ID = "p_" + sha256_hex(PEPPER)[:8]


def apikey_message(**overrides: Any) -> dict[str, Any]:
    message = {
        "key_id": KEY_ID,
        "secret_sha256": sha256_hex(PEPPER + SECRET.encode()),
        "pepper_id": PEPPER_ID,
        "source_id": SOURCE_ID,
        "tenant_id": TENANT,
        "status": "active",
        "quota_eps": 500,
        "created_at": "2026-09-28T10:00:00.000000000Z",
    }
    message.update(overrides)
    return message


def source_message(**overrides: Any) -> dict[str, Any]:
    message = {
        "source_id": SOURCE_ID,
        "tenant_id": TENANT,
        "vendor": "custom",
        "zone": "dmz",
        "transport": "http_hec_event",
        "contract_id": "authsrv",
        "expected_eps": 2.0,
        "salt_buckets": 1,
        "status": "active",
    }
    message.update(overrides)
    return message
