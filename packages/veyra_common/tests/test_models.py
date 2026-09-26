"""Round-trip every fixture through its model (S0.2 task 7).

Fixtures are the cross-track contract surface: if a producer changes one, every
consumer that tests against it fails on purpose (01_TEAM_GUIDE §7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter
from veyra_common.models import (
    ApiKeyMessage,
    AuditRecord,
    ContractMessage,
    DlqRecord,
    EnrichMessage,
    Envelope,
    LineageRecord,
    NormEvent,
    Receipt,
    RoutesMessage,
    ShadowRecord,
    SignedRoot,
    SourceMessage,
    Ulpf,
    VaultIndexEvent,
    VaultIndexRecord,
    VaultIndexSegment,
    VocabMessage,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

CASES: list[tuple[str, type[BaseModel]]] = [
    ("envelope.json", Envelope),
    ("ulpf.json", Ulpf),
    ("norm_event.json", NormEvent),
    ("lineage.json", LineageRecord),
    ("dlq.json", DlqRecord),
    ("shadow.json", ShadowRecord),
    ("vault_index_event.json", VaultIndexEvent),
    ("vault_index_segment.json", VaultIndexSegment),
    ("receipt.json", Receipt),
    ("audit.json", AuditRecord),
    ("signed_root.json", SignedRoot),
    ("control_apikey.json", ApiKeyMessage),
    ("control_source.json", SourceMessage),
    ("control_contract.json", ContractMessage),
    ("control_vocab.json", VocabMessage),
    ("control_enrich.json", EnrichMessage),
    ("control_routes.json", RoutesMessage),
]


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_every_model_has_a_fixture() -> None:
    present = {p.name for p in FIXTURES.glob("*.json")} - {"envelope_vectors.json"}
    assert present == {name for name, _ in CASES}


@pytest.mark.parametrize(("name", "model"), CASES, ids=[c[0] for c in CASES])
def test_round_trip(name: str, model: type[BaseModel]) -> None:
    data = _load(name)
    obj = model.model_validate(data)
    again = json.loads(obj.model_dump_json())
    assert again == data, f"{name} is not round-trip stable"
    assert model.model_validate(again) == obj


def test_vault_index_union_discriminates() -> None:
    adapter = TypeAdapter(VaultIndexRecord)
    assert isinstance(adapter.validate_python(_load("vault_index_event.json")), VaultIndexEvent)
    assert isinstance(adapter.validate_python(_load("vault_index_segment.json")), VaultIndexSegment)


def test_unknown_field_is_rejected_on_closed_records() -> None:
    """Consumers must notice an unexpected producer field, not silently drop it."""
    bad = _load("lineage.json") | {"surprise": 1}
    with pytest.raises(ValueError, match="surprise"):
        LineageRecord.model_validate(bad)


def test_norm_event_keeps_open_ocsf_fields() -> None:
    """The OCSF body is open by design: unknown class fields must survive."""
    data = _load("norm_event.json") | {"user": {"name": "a.sharma"}}
    event = NormEvent.model_validate(data)
    assert json.loads(event.model_dump_json())["user"] == {"name": "a.sharma"}


def test_envelope_hash_and_key() -> None:
    env = Envelope.model_validate(_load("envelope.json"))
    assert env.hash_matches()
    assert env.raw_bytes.startswith(b"<134>Sep 26")
    assert env.kafka_key() == "src_authsrv_01"
    salted = env.model_copy(update={"salt": 2})
    assert salted.kafka_key() == "src_authsrv_01#2"


def test_envelope_rejects_bad_hash_shape() -> None:
    with pytest.raises(ValueError, match="raw_sha256"):
        Envelope.model_validate(_load("envelope.json") | {"raw_sha256": "NOTAHASH"})


def test_parity_vectors_are_self_consistent() -> None:
    """A1 asserts Vector reproduces these; here we assert they are internally valid."""
    payload = json.loads((FIXTURES / "envelope_vectors.json").read_text())
    names = set()
    for vector in payload["vectors"]:
        env = Envelope.model_validate(vector["envelope"])
        assert env.hash_matches()
        assert env.raw_len == len(env.raw_bytes)
        names.add(vector["name"])
    assert names == {"t3_multiline_syslog_tcp", "sshd_datagram_udp", "cef_http_hec_event"}
