"""A5 in the service: replay revisions and shadow records, at the level that produces Kafka records.

Two properties are worth this much care:

* **a replay must not invent a revision.** control-api computes it from the lineage index and sends
  it; if the normalizer recomputed or defaulted it, a record re-processed after a crash would land
  under a different revision and B1's ReplacingMergeTree would keep both — doubling history.
* **a shadow record belongs to the transaction of the event it describes.** A shadow row that
  survived a crash the event did not would report a comparison that never happened.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from normalizer.__main__ import NormalizerSettings, _parse, _shadow_record, build_outputs

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope, HecMeta, LineageRecord, ShadowRecord
from veyra_engine import Engine, EngineContext, mini_compile

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
CONTRACTS = REPO / "packages" / "veyra_engine" / "tests" / "contracts"
RECEIVED = "2026-09-26T14:10:00.000000000Z"
JOB = "rj_abc12345"


class FakeMessage:
    """Just the accessors `build_outputs` reads."""

    def __init__(self, value: bytes, *, topic: str = "raw.custom", offset: int = 17) -> None:
        self._value, self._topic, self._offset = value, topic, offset

    def value(self) -> bytes:
        return self._value

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return self._offset


def compiled(name: str) -> dict[str, Any]:
    return mini_compile((CONTRACTS / name).read_text())


def t3_line() -> bytes:
    return split_lines((CORPUS / "authsrv_t3_failed.log").read_bytes())[0].raw


def envelope(**overrides: Any) -> Envelope:
    fields: dict[str, Any] = {
        "collector_id": "edge-dmz-01",
        "transport": "syslog_tcp",
        "framing_method": "multiline_join",
        "source_id": "src_authsrv_01",
        "tenant_id": "t_maha_power",
        "vendor": "custom",
        "zone": "dmz",
        "event_uid": "0192a4f0-0000-7000-8000-000000000001",
        "received_time": RECEIVED,
    }
    fields.update(overrides)
    return stamp(t3_line(), **fields)


def replayed(event: Envelope, *, revision: int = 2, job_id: str = JOB) -> bytes:
    payload = event.model_dump(mode="json")
    payload["replay"] = {
        "job_id": job_id,
        "revision": revision,
        "supersedes": f"{event.event_uid}@{revision - 1}",
    }
    return json.dumps(payload).encode()


def engine_with(active: str, candidate: str | None = None, **ctx: Any) -> Engine:
    engine = Engine(EngineContext(**ctx))
    engine.load([compiled(active)])
    if candidate is not None:
        compiled_candidate = compiled(candidate)
        engine.set_candidate(compiled_candidate, str(compiled_candidate["contract"]))
    return engine


def records_of(outputs: list[Any], prefix: str) -> list[dict[str, Any]]:
    return [json.loads(out.value) for out in outputs if out.topic.startswith(prefix)]


# ---------------------------------------------------------------- the replay block
def block_of(raw: bytes) -> Any:
    return _parse(raw)[1]


def test_a_replay_envelope_validates_despite_the_extra_block() -> None:
    """`Envelope` forbids extra fields, so this is the whole reason `_parse` exists."""
    event, block = _parse(replayed(envelope(), revision=3))
    assert event.event_uid == envelope().event_uid
    assert block is not None
    assert (block.job_id, block.revision) == (JOB, 3)
    assert block.supersedes.endswith("@2")


def test_no_block_on_an_ordinary_event() -> None:
    assert block_of(envelope().model_dump_json().encode()) is None


def test_a_malformed_block_is_refused_rather_than_guessed() -> None:
    """A block missing `supersedes` must not quietly become revision 2 of something."""
    payload = envelope().model_dump(mode="json")
    payload["replay"] = {"job_id": JOB, "revision": 2}
    assert block_of(json.dumps(payload).encode()) is None


def test_a_revision_below_two_is_refused() -> None:
    """Revision 1 is the original; a replay that claims it would overwrite the real record."""
    payload = envelope().model_dump(mode="json")
    payload["replay"] = {"job_id": JOB, "revision": 1, "supersedes": "x@0"}
    assert block_of(json.dumps(payload).encode()) is None


# ---------------------------------------------------------------- AC3 semantics
def test_a_replayed_event_keeps_its_uid_and_takes_the_revision_it_was_sent() -> None:
    engine = engine_with("authsrv_v2.yaml")
    event = envelope()
    outputs = build_outputs(
        engine, FakeMessage(replayed(event, revision=2), topic="replay.raw"), NormalizerSettings()
    )

    norm = records_of(outputs, "norm.")[0]
    assert norm["ulpf"]["event_uid"] == event.event_uid, "a replay re-emits the same event"
    assert norm["ulpf"]["revision"] == 2
    assert norm["ulpf"]["supersedes"] == f"{event.event_uid}@1"
    assert norm["ulpf"]["replay"] is True
    assert norm["ulpf"]["tier"] == 1, "replay uses the contract active now"

    lineage = LineageRecord.model_validate(records_of(outputs, "lineage")[0])
    assert lineage.replay_job_id == JOB, "control-api counts exactly this field"
    assert lineage.revision == 2
    assert lineage.replay is True


def test_the_same_replay_record_twice_yields_the_same_revision() -> None:
    """Idempotence: reprocessing after a crash must not produce a second, different revision."""
    engine = engine_with("authsrv_v2.yaml")
    message = FakeMessage(replayed(envelope(), revision=4), topic="replay.raw")
    first = records_of(build_outputs(engine, message, NormalizerSettings()), "norm.")[0]
    second = records_of(build_outputs(engine, message, NormalizerSettings()), "norm.")[0]
    assert first["ulpf"]["revision"] == second["ulpf"]["revision"] == 4
    assert first["ulpf"]["supersedes"] == second["ulpf"]["supersedes"]


def test_a_replay_preserves_custody_and_the_senders_hec_claims() -> None:
    """A batch-uploaded event that came back as `realtime` would have lost its provenance (A2)."""
    engine = engine_with("authsrv_v2.yaml")
    event = envelope(
        custody="post_hoc",
        transport="http_batch",
        framing_method="batch_line",
        hec_meta=HecMeta(host="authsrv-01", sourcetype="maha_authsrv"),
    )
    outputs = build_outputs(
        engine, FakeMessage(replayed(event), topic="replay.raw"), NormalizerSettings()
    )
    norm = records_of(outputs, "norm.")[0]
    assert norm["ulpf"]["custody"] == "post_hoc"
    assert norm["unmapped"]["hec_meta"]["host"] == "authsrv-01"


def test_an_ordinary_event_stays_at_revision_one() -> None:
    engine = engine_with("authsrv_v2.yaml")
    outputs = build_outputs(
        engine, FakeMessage(envelope().model_dump_json().encode()), NormalizerSettings()
    )
    norm = records_of(outputs, "norm.")[0]
    assert norm["ulpf"]["revision"] == 1
    assert norm["ulpf"]["replay"] is False
    assert norm["ulpf"]["supersedes"] is None
    assert LineageRecord.model_validate(records_of(outputs, "lineage")[0]).replay_job_id is None


def test_a_malformed_block_still_delivers_the_event() -> None:
    """P2: a bad replay block degrades to an ordinary event, it never drops one."""
    engine = engine_with("authsrv_v2.yaml")
    payload = envelope().model_dump(mode="json")
    payload["replay"] = {"job_id": JOB}
    outputs = build_outputs(
        engine, FakeMessage(json.dumps(payload).encode(), topic="replay.raw"), NormalizerSettings()
    )
    norm = records_of(outputs, "norm.")[0]
    assert norm["ulpf"]["tier"] == 1, "the event is still normalized"
    assert norm["ulpf"]["replay"] is False, "but it is not credited as a replay"


# ---------------------------------------------------------------- shadow records
def test_no_candidate_means_no_shadow_record() -> None:
    engine = engine_with("authsrv.yaml")
    outputs = build_outputs(
        engine, FakeMessage(envelope().model_dump_json().encode()), NormalizerSettings()
    )
    assert not [out for out in outputs if out.topic == "shadow"]


def test_a_candidate_produces_one_shadow_record_beside_the_event() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml")
    event = envelope()
    message = FakeMessage(event.model_dump_json().encode())
    outputs = build_outputs(engine, message, NormalizerSettings())

    shadows = [out for out in outputs if out.topic == "shadow"]
    assert len(shadows) == 1
    assert shadows[0].key == event.event_uid
    record = ShadowRecord.model_validate_json(shadows[0].value)
    assert (record.active_tier, record.candidate_tier) == (3, 1)
    assert record.active_ref == "authsrv@1"
    assert record.candidate_ref == "authsrv@2"
    assert "user.name" in record.changed_fields
    assert record.regressions == []
    assert record.produced_at == RECEIVED

    # And the delivered event is the active one: tier 3, no class claimed.
    norm = records_of(outputs, "norm.")[0]
    assert norm["ulpf"]["tier"] == 3
    assert norm["class_uid"] == 0
    assert [out.topic for out in outputs if out.topic.startswith("norm.")] == ["norm.uncategorized"]


def test_a_shadow_that_overruns_its_budget_is_dropped_not_emitted() -> None:
    engine = engine_with("authsrv.yaml", "authsrv_v2.yaml", shadow_budget_us=1)
    event = envelope()
    assert _shadow_record(engine, event, engine.normalize(event)) is None
    message = FakeMessage(event.model_dump_json().encode())
    outputs = build_outputs(engine, message, NormalizerSettings())
    assert not [out for out in outputs if out.topic == "shadow"]
    assert records_of(outputs, "norm."), "the event itself is still delivered"
