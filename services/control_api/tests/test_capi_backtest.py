"""Backtest (C2): candidate vs active over real stored events, automatically at canary."""

from __future__ import annotations

from capi_helpers import T3_SIG, activate, submit, t3_events, version
from control_api.tables import DriftItem
from sqlmodel import Session as DbSession


def stored_t3(index, raw_store, count: int = 8) -> None:
    for ref, envelope in t3_events(count):
        index.events.setdefault(T3_SIG, []).append(ref)
        raw_store.by_uid[ref.event_uid] = envelope


def open_drift(ctx) -> None:
    with DbSession(ctx.engine) as db:
        db.add(
            DriftItem(
                drift_id="dr_1", source_id="src_authsrv_01", tenant_id="t_maha_power",
                template_sig=T3_SIG, first_seen="2026-09-26T14:05:11Z",
                last_seen="2026-09-26T14:05:11Z", count=8,
            )
        )  # fmt: skip
        db.commit()


def test_v2_at_canary_upgrades_the_eight_t3_events(
    client, authsrv_source, as_user, ctx, index, raw_store
) -> None:
    """C2 AC2 (standalone half): the backtest finds the drift sig's events by itself."""
    stored_t3(index, raw_store)
    open_drift(ctx)
    activate(client, as_user, 1)
    as_user("author@maha")
    body = submit(client, version(2))
    result = body["backtest"]
    assert (result["n"], result["upgraded"], result["regressed"]) == (8, 8, 0)
    assert result["tier_after"] == {"1": 8}
    assert result["sigs"] == [T3_SIG] and result["error"] is None
    assert all(example["provenance_ok"] for example in result["examples"])


def test_the_endpoint_backtests_requested_sigs_and_pasted_samples(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    stored_t3(index, raw_store, count=3)
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    sample = (
        '<134>Sep 26 14:05:09 fw01 app[233]: {"msg":"user=x.y OK login from 10.0.0.1 via 10.2.3.4"}'
    )
    response = client.post(
        "/contracts/authsrv/versions/2/backtest",
        json={"template_sigs": [T3_SIG], "samples": [sample]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["n"], result["samples"], result["events_found"]) == (4, 1, 3)
    stored = client.get("/contracts/authsrv/versions/2").json()["backtest"]
    assert stored["n"] == 4


def test_the_backtest_is_capped_at_backtest_max(
    client, authsrv_source, as_user, ctx, index, raw_store
) -> None:
    stored_t3(index, raw_store)
    ctx.cfg.backtest_max = 5
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    result = client.post(
        "/contracts/authsrv/versions/2/backtest", json={"template_sigs": [T3_SIG]}
    ).json()
    assert result["n"] == 5


def test_an_evidence_outage_is_reported_not_raised(
    client, authsrv_source, as_user, ctx, index
) -> None:
    index.down = True
    open_drift(ctx)
    activate(client, as_user, 1)
    as_user("author@maha")
    body = submit(client, version(2))
    assert body["state"] == "canary"
    assert "connection refused" in body["backtest"]["error"]


def test_raw_past_retention_is_counted_as_missing(
    client, authsrv_source, as_user, index, raw_store
) -> None:
    stored_t3(index, raw_store, count=4)
    del raw_store.by_uid[index.events[T3_SIG][0].event_uid]
    activate(client, as_user, 1)
    as_user("author@maha")
    submit(client, version(2))
    result = client.post(
        "/contracts/authsrv/versions/2/backtest", json={"template_sigs": [T3_SIG]}
    ).json()
    assert (result["events_found"], result["raw_missing"], result["n"]) == (4, 1, 3)


def test_backtest_needs_a_signed_in_actor(client, authsrv_source, as_user) -> None:
    submit(client, version(1))
    as_user("admin@veyra")
    assert client.post("/contracts/authsrv/versions/1/backtest", json={}).status_code == 200
    client.post("/auth/logout")
    assert client.post("/contracts/authsrv/versions/1/backtest", json={}).status_code == 401
