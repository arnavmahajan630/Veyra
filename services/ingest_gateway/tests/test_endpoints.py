"""The three endpoints: what reaches Kafka, and what the client is told.

Every assertion here is about the envelope, because the envelope is the evidence (P1). A 200 that
produced the wrong bytes, the wrong source or the wrong custody would be worse than a 500.
"""

from __future__ import annotations

import base64
import io
import json
from typing import Any

from fastapi.testclient import TestClient
from gateway_helpers import KEY_ID, SOURCE_ID, TENANT, source_message
from ingest_gateway.app import GatewayContext
from ingest_gateway.registry import KeyRegistry

from veyra_common.framing import split_lines
from veyra_common.hashing import sha256_hex
from veyra_common.models import Envelope

T3_LINE = (
    '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login from '
    '103.21.4.77 via 10.2.3.4 attempts:1"} | trace='
)
T3_CONTINUATION = "  at com.x.Auth.login(Auth.java:88)"


def envelopes_of(fake: Any) -> list[Envelope]:
    return [Envelope.model_validate_json(value) for _, _, value in fake.messages]


# ---------------------------------------------------------------- HEC event
def test_event_endpoint_stamps_one_envelope_per_object(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    body = '{"event":"one"}{"event":"two"}'
    response = client.post("/services/collector/event", content=body, headers=auth)
    assert response.status_code == 200
    assert response.json()["text"] == "Success"
    assert response.json()["events"] == 2

    envelopes = envelopes_of(fake)
    assert [e.raw_bytes for e in envelopes] == [b"one", b"two"]
    assert {e.transport for e in envelopes} == {"http_hec_event"}
    assert {e.framing.method for e in envelopes} == {"http_body"}
    assert all(e.hash_matches() for e in envelopes)


def test_the_envelope_carries_the_key_and_the_source(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    envelope = envelopes_of(fake)[0]
    assert envelope.source_id == SOURCE_ID
    assert envelope.tenant_id == TENANT
    assert envelope.auth.method == "api_key"
    assert envelope.auth.key_id == KEY_ID
    assert envelope.custody == "realtime"
    assert envelope.collector_id == "gw-test"


def test_the_topic_is_raw_vendor(client: TestClient, fake: Any, auth: dict[str, str]) -> None:
    client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert fake.topics() == ["raw.custom"]
    assert fake.messages[0][1] == SOURCE_ID, "the Kafka key is the source_id (IF-TOPICS)"


def test_a_salted_source_uses_the_salted_key(
    client: TestClient, fake: Any, auth: dict[str, str], registry: KeyRegistry
) -> None:
    """A heavy hitter's key is source_id#n — the gateway must not invent a salt nobody gave it."""
    registry._handle(f"source:{SOURCE_ID}", source_message(salt_buckets=4))
    client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    assert fake.messages[0][1] == SOURCE_ID, "salting is A6/edge policy, not the gateway's to guess"


def test_hec_meta_reaches_the_envelope(client: TestClient, fake: Any, auth: dict[str, str]) -> None:
    body = json.dumps({"event": "line", "time": 1790000000.5, "host": "authsrv-01"})
    client.post("/services/collector/event", content=body, headers=auth)
    envelope = envelopes_of(fake)[0]
    assert envelope.hec_meta is not None
    assert envelope.hec_meta.time == 1790000000.5
    assert envelope.hec_meta.host == "authsrv-01"
    # The sender's claim must not become the received_time VEYRA stamped.
    assert envelope.received_time.startswith("20")


def test_an_object_event_is_serialized_deterministically(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    client.post(
        "/services/collector/event",
        content='{"event":{"b":2,"a":1}}{"event":{"a":1,"b":2}}',
        headers=auth,
    )
    envelopes = envelopes_of(fake)
    assert envelopes[0].raw_sha256 == envelopes[1].raw_sha256


def test_a_malformed_body_is_400_and_produces_nothing(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    response = client.post("/services/collector/event", content="{not json", headers=auth)
    assert response.status_code == 400
    assert response.json()["code"] == 6
    assert not fake.messages


# ---------------------------------------------------------------- HEC raw
def test_raw_endpoint_uses_the_shared_framing_rule(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    """The same continuation rule as the edge: a `  at …` line belongs to the event above it."""
    body = f"{T3_LINE}\n{T3_CONTINUATION}\nsecond line\n".encode()
    response = client.post("/services/collector/raw", content=body, headers=auth)
    assert response.status_code == 200

    expected = split_lines(body)
    envelopes = envelopes_of(fake)
    assert len(envelopes) == len(expected) == 2
    assert [e.raw_bytes for e in envelopes] == [f.raw for f in expected]
    assert envelopes[0].framing.parts == 2
    assert envelopes[0].framing.method == "multiline_join"
    assert envelopes[1].framing.method == "newline"
    assert {e.transport for e in envelopes} == {"http_hec_raw"}


def test_raw_endpoint_rejects_an_empty_body(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    response = client.post("/services/collector/raw", content=b"", headers=auth)
    assert response.status_code == 400
    assert not fake.messages


def test_an_oversized_event_is_truncated_and_says_so(
    client: TestClient, fake: Any, auth: dict[str, str], ctx: GatewayContext
) -> None:
    """The cap is on parsing, not on honesty: the hash covers the bytes actually stored."""
    ctx.cfg.max_event_bytes = 64
    client.post("/services/collector/raw", content=b"a" * 500, headers=auth)
    envelope = envelopes_of(fake)[0]
    assert envelope.framing.truncated is True
    assert envelope.raw_len == 64
    assert envelope.hash_matches()


def test_a_body_over_the_cap_is_413(
    client: TestClient, fake: Any, auth: dict[str, str], ctx: GatewayContext
) -> None:
    ctx.cfg.gateway_max_body_bytes = 128
    response = client.post("/services/collector/raw", content=b"x" * 200, headers=auth)
    assert response.status_code == 413
    assert not fake.messages


# ---------------------------------------------------------------- batch
def test_batch_upload_sets_post_hoc_custody_and_returns_a_manifest(
    client: TestClient, fake: Any, auth: dict[str, str]
) -> None:
    payload = b"line one\nline two\nline three\n"
    response = client.post(
        "/v1/batch",
        files={"file": ("history.log", io.BytesIO(payload), "text/plain")},
        headers=auth,
    )
    assert response.status_code == 200
    manifest = response.json()["manifest"]
    assert manifest["count"] == 3
    assert manifest["sha256_of_file"] == sha256_hex(payload)
    assert manifest["filename"] == "history.log"
    assert manifest["custody"] == "post_hoc"

    envelopes = envelopes_of(fake)
    assert manifest["first_event_uid"] == envelopes[0].event_uid
    assert manifest["last_event_uid"] == envelopes[-1].event_uid
    assert {e.custody for e in envelopes} == {"post_hoc"}
    assert {e.framing.method for e in envelopes} == {"batch_line"}
    assert {e.transport for e in envelopes} == {"http_batch"}


def test_batch_requires_auth(client: TestClient, fake: Any) -> None:
    response = client.post("/v1/batch", files={"file": ("x.log", io.BytesIO(b"a\n"), "text/plain")})
    assert response.status_code == 401
    assert not fake.messages


def test_batch_continuation_lines_join(client: TestClient, fake: Any, auth: dict[str, str]) -> None:
    payload = f"{T3_LINE}\n{T3_CONTINUATION}\n".encode()
    upload = {"file": ("t3.log", io.BytesIO(payload), "text/plain")}
    client.post("/v1/batch", files=upload, headers=auth)
    envelopes = envelopes_of(fake)
    assert len(envelopes) == 1
    assert envelopes[0].framing.parts == 2
    assert base64.b64decode(envelopes[0].raw_b64) == f"{T3_LINE}\n{T3_CONTINUATION}".encode()
