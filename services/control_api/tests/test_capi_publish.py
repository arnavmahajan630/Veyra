"""IF-CONTROL messages from table rows, republish_all, and the synchronous publisher."""

from __future__ import annotations

import json

import pytest
from control_api.messages import republish_all, source_message
from control_api.publisher import ControlPublisher, PublishError
from control_api.seed_docs import load_seed_docs
from control_api.tables import ApiKey, Contract, ContractVersion, Source, Tenant

AT = "2026-09-27T00:00:00.000000000Z"


def add_world(db) -> None:
    db.add(Tenant(id="t_a", name="A", created_at=AT))
    db.flush()  # parents before children: inserts aren't ordered across tables
    db.add(
        Source(
            id="src_a", tenant_id="t_a", name="A", vendor="custom", zone="dmz",
            transport="http_push", created_at=AT,
        )
    )  # fmt: skip
    db.flush()
    for key_id, status in (("k_AAAAAAAA", "active"), ("k_BBBBBBBB", "revoked")):
        db.add(
            ApiKey(
                key_id=key_id, source_id="src_a", tenant_id="t_a", secret_sha256="0" * 64,
                pepper_id="p_1", quota_eps=500, status=status, created_by="admin@veyra",
                created_at=AT,
            )
        )  # fmt: skip
    db.add(Contract(id="c1", tenant_id="t_a", active_version=1, canary_version=2))
    db.flush()
    for version, state in ((1, "active"), (2, "canary")):
        compiled = {"contract": "c1", "version": version, "tenant": "t_a", "sources": ["src_a"]}
        db.add(
            ContractVersion(
                contract_id="c1", version=version, state=state, yaml="",
                compiled_json=json.dumps(compiled), author="author@maha", created_at=AT,
            )
        )  # fmt: skip
    db.commit()


def test_seed_documents_load() -> None:
    docs = load_seed_docs()
    assert {v.name for v in docs.vocab} == {"status_words", "severity_words"}
    status = next(v for v in docs.vocab if v.name == "status_words")
    assert status.entries["FAILED"] == {"status_id": 2}
    assert {e.name for e in docs.enrich} == {"asset_inventory", "zone_map"}
    assert [r.id for r in docs.routes.routes] == ["wazuh_main", "partner_masked"]


def test_http_push_is_published_as_the_hec_event_transport(db) -> None:
    add_world(db)
    source = db.get(Source, "src_a")
    assert source is not None
    assert source_message(source).transport == "http_hec_event"


def test_republish_all_publishes_the_complete_control_state(db, producer) -> None:
    add_world(db)
    publisher = ControlPublisher(producer, timeout_s=1)
    count = republish_all(db, publisher, load_seed_docs(), AT)
    latest = producer.latest("control")
    assert set(latest) == {
        "source:src_a", "apikey:k_AAAAAAAA", "contract:c1", "vocab:status_words",
        "vocab:severity_words", "enrich:asset_inventory", "enrich:zone_map", "routes",
    }  # fmt: skip
    assert count == len(latest)
    contract = latest["contract:c1"]
    assert (contract["version"], contract["state"], contract["sources"]) == (1, "active", ["src_a"])
    assert contract["candidate"]["version"] == 2
    assert contract["published_at"] == AT


def test_a_none_message_is_a_tombstone(producer) -> None:
    ControlPublisher(producer, timeout_s=1).publish("source:x", None)
    assert producer.messages == [("control", "source:x", None)]


def test_publish_raises_when_the_broker_rejects(producer) -> None:
    producer.fail_with = "broker down"
    with pytest.raises(PublishError, match="broker down"):
        ControlPublisher(producer, timeout_s=1).publish("source:x", None)


def test_publish_raises_when_messages_stay_unacknowledged(producer) -> None:
    producer.unacked = 1
    with pytest.raises(PublishError, match="1 control message"):
        ControlPublisher(producer, timeout_s=1).publish("source:x", None)
