"""The gateway's envelopes must be indistinguishable from the edge's.

S0 wrote three parity vectors; A1's integration test asserts Vector's VRL reproduces them, and
this asserts the gateway's HTTP path does too. The one that matters most is `cef_http_hec_event`:
an event arriving through this very service, whose seal has to match byte for byte, because a
verifier re-hashing the archive cannot know which path an event took.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from ingest_gateway.app import GatewayContext

from veyra_common.models import Envelope

VECTORS = (
    Path(__file__).resolve().parents[3]
    / "packages"
    / "veyra_common"
    / "fixtures"
    / "envelope_vectors.json"
)


def vector(name: str) -> Envelope:
    payload = json.loads(VECTORS.read_text())
    for entry in payload["vectors"]:
        if entry["name"] == name:
            return Envelope.model_validate(entry["envelope"])
    raise AssertionError(f"no parity vector named {name}")


def test_the_hec_event_vector_is_reproduced_byte_for_byte(
    client: TestClient, fake: object, auth: dict[str, str]
) -> None:
    reference = vector("cef_http_hec_event")
    body = json.dumps({"event": reference.raw_bytes.decode()})
    response = client.post("/services/collector/event", content=body, headers=auth)
    assert response.status_code == 200

    produced = Envelope.model_validate_json(fake.messages[0][2])  # type: ignore[attr-defined]
    assert produced.raw_b64 == reference.raw_b64
    assert produced.raw_sha256 == reference.raw_sha256
    assert produced.raw_len == reference.raw_len
    assert produced.transport == reference.transport == "http_hec_event"
    assert produced.framing.method == reference.framing.method == "http_body"
    assert produced.hash_matches()


def test_a_multiline_event_matches_the_edges_join(
    client: TestClient, fake: object, auth: dict[str, str]
) -> None:
    """The edge joins continuation lines with a single \\n; the raw endpoint must do the same."""
    reference = vector("t3_multiline_syslog_tcp")
    response = client.post("/services/collector/raw", content=reference.raw_bytes, headers=auth)
    assert response.status_code == 200

    produced = Envelope.model_validate_json(fake.messages[0][2])  # type: ignore[attr-defined]
    assert produced.raw_b64 == reference.raw_b64
    assert produced.raw_sha256 == reference.raw_sha256
    assert produced.framing.parts == reference.framing.parts == 2
    # Only the transport differs — the same bytes arrived over HTTP rather than TCP.
    assert produced.transport == "http_hec_raw"


def test_every_stamped_field_a_verifier_reads_is_present(
    client: TestClient, fake: object, auth: dict[str, str], ctx: GatewayContext
) -> None:
    """A gateway envelope must satisfy IF-ENVELOPE in full, not just the fields we happen to set."""
    client.post("/services/collector/event", content='{"event":"x"}', headers=auth)
    produced = json.loads(fake.messages[0][2])  # type: ignore[attr-defined]
    for field in Envelope.model_fields:
        assert field in produced, f"IF-ENVELOPE field {field} missing from a gateway envelope"
    assert produced["v"] == 1
    assert produced["peer_ip"], "the client address is evidence too"
