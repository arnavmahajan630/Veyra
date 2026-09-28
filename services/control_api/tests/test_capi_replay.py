"""Replay jobs (C2): stored events back through the active contract, revision + 1."""

from __future__ import annotations

import json

from capi_helpers import T3_SIG, activate, t3_events
from control_api.tables import DriftItem
from sqlmodel import Session as DbSession


def stored_t3(index, raw_store, count: int = 8) -> None:
    for ref, envelope in t3_events(count):
        index.events.setdefault(T3_SIG, []).append(ref)
        raw_store.by_uid[ref.event_uid] = envelope


def promoted_v2(client, as_user) -> None:
    activate(client, as_user, 1)
    activate(client, as_user, 2)


def replay(client, **body) -> dict:
    response = client.post("/replay", json={"contract_id": "authsrv", **body})
    assert response.status_code == 202, response.text
    job: dict = client.get(f"/replay/{response.json()['job_id']}").json()
    return job


def test_eight_events_replay_to_done(
    client, authsrv_source, as_user, index, raw_store, producer
) -> None:
    """C2 AC4 (standalone half): the job reaches done with normalized = 8."""
    stored_t3(index, raw_store)
    promoted_v2(client, as_user)
    job = replay(client, template_sigs=[T3_SIG])
    assert (job["state"], job["total"], job["published"], job["normalized"]) == ("done", 8, 8, 8)
    assert job["finished_at"] is not None

    sent = [(key, json.loads(value)) for topic, key, value in producer.messages
            if topic == "replay.raw"]  # fmt: skip
    assert len(sent) == 8 and {key for key, _ in sent} == {"src_authsrv_01"}
    first = sent[0][1]
    assert first["replay"] == {
        "job_id": job["job_id"],
        "revision": 2,
        "supersedes": f"{first['event_uid']}@1",
    }
    assert first["raw_b64"] == raw_store.by_uid[first["event_uid"]].raw_b64


def test_too_few_normalized_times_out(
    client, authsrv_source, as_user, index, raw_store, watcher
) -> None:
    stored_t3(index, raw_store)
    watcher.normalized = 5
    promoted_v2(client, as_user)
    job = replay(client, template_sigs=[T3_SIG])
    assert (job["state"], job["normalized"]) == ("timed_out", 5)


def test_the_sigs_default_to_the_sources_drift_items(
    client, authsrv_source, as_user, ctx, index, raw_store
) -> None:
    stored_t3(index, raw_store, count=2)
    with DbSession(ctx.engine) as db:
        db.add(
            DriftItem(
                drift_id="dr_1", source_id="src_authsrv_01", tenant_id="t_maha_power",
                template_sig=T3_SIG, state="resolved", first_seen="x", last_seen="x",
            )
        )  # fmt: skip
        db.commit()
    promoted_v2(client, as_user)
    job = replay(client)
    assert job["params"]["template_sigs"] == [T3_SIG] and job["state"] == "done"


def test_source_and_time_window_filter_the_events(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    stored_t3(index, raw_store, count=3)
    index.events[T3_SIG] = [
        type(r)(**{**r.__dict__, "produced_at": f"2026-09-26T14:0{i}:00Z"})
        for i, r in enumerate(index.events[T3_SIG])
    ]
    promoted_v2(client, as_user)
    job = replay(client, template_sigs=[T3_SIG], **{"from": "2026-09-26T14:01:00Z"})
    assert job["total"] == 2
    assert replay(client, template_sigs=[T3_SIG], source_id="src_other")["state"] == "failed"


def test_refusals(client, authsrv_source, as_user) -> None:
    assert client.post("/replay", json={"contract_id": "authsrv"}).status_code == 404
    activate(client, as_user, 1)
    assert client.post("/replay", json={"contract_id": "authsrv"}).status_code == 422
    as_user("author@maha")
    assert client.post("/replay", json={"contract_id": "linux_sshd"}).status_code == 404


def test_an_evidence_outage_fails_the_job(client, authsrv_source, as_user, index) -> None:
    index.down = True
    promoted_v2(client, as_user)
    job = replay(client, template_sigs=[T3_SIG])
    assert job["state"] == "failed" and "connection refused" in job["detail"]


def test_jobs_are_listed_newest_first_and_scoped(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    stored_t3(index, raw_store, count=1)
    promoted_v2(client, as_user)
    first = replay(client, template_sigs=[T3_SIG])["job_id"]
    second = replay(client, template_sigs=[T3_SIG])["job_id"]
    listed = [j["job_id"] for j in client.get("/replay", params={"contract_id": "authsrv"}).json()]
    assert listed[:2] == [second, first]
    as_user("author@maha")
    assert len(client.get("/replay").json()) == 2
    as_user("admin@veyra")
    assert client.get(f"/replay/{first}").status_code == 200
