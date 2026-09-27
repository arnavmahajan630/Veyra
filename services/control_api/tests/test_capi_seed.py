"""The pre-demo world (04_DEMO_SCRIPT §2), reset back to it, and the seed CLI (C1 AC4)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from control_api.contracts_repo import commit_all, head_commit
from control_api.security import verify_password
from control_api.seed import SeedSummary, main, reset_state, seed
from control_api.tables import ApiKey, Contract, ContractVersion, Source, Tenant, User
from sqlmodel import Session as DbSession
from sqlmodel import select


def snapshot(engine) -> dict[str, Any]:
    with DbSession(engine) as db:
        return {
            "tenants": sorted((t.id, t.name) for t in db.exec(select(Tenant)).all()),
            "users": sorted((u.email, u.role, u.tenant_id) for u in db.exec(select(User)).all()),
            "sources": sorted(
                tuple(sorted(s.model_dump().items())) for s in db.exec(select(Source)).all()
            ),
            "contracts": sorted(
                (c.id, c.tenant_id, c.active_version, c.canary_version)
                for c in db.exec(select(Contract)).all()
            ),
            "versions": sorted(
                (v.contract_id, v.version, v.state, v.compiled_json, v.git_commit)
                for v in db.exec(select(ContractVersion)).all()
            ),
            "keys": len(db.exec(select(ApiKey)).all()),
        }


def test_seed_creates_the_pre_demo_world(engine, cfg, seeded_repo: Path, clock) -> None:
    with DbSession(engine) as db:
        summary = seed(db, cfg=cfg, now_ns=clock())
        db.commit()
    assert summary == SeedSummary(tenants=2, users=3, sources=2, contracts=2)
    snap = snapshot(engine)
    assert snap["tenants"] == [
        ("t_maha_power", "Maha Power Corp"),
        ("t_ntro_core", "NTRO Core Ops"),
    ]
    assert snap["users"] == [
        ("admin@veyra", "admin", "*"),
        ("approver@veyra", "pack_approver", "*"),
        ("author@maha", "pack_author", "t_maha_power"),
    ]
    with DbSession(engine) as db:
        sources = {s.id: s for s in db.exec(select(Source)).all()}
        versions = db.exec(select(ContractVersion)).all()
    assert set(sources) == {"src_fw_dmz_01", "src_lnx_core_07"}
    assert (sources["src_fw_dmz_01"].match_kind, sources["src_fw_dmz_01"].match_value) == (
        "syslog_host",
        "fw-dmz-01",
    )
    assert {v.contract_id for v in versions} == {"linux_sshd", "acme_ngfw_cef"}
    assert all(v.state == "active" and v.git_commit == head_commit(seeded_repo) for v in versions)
    assert all(json.loads(v.compiled_json or "{}")["templates"] for v in versions)


def test_seeded_users_sign_in_with_the_demo_password(engine, cfg, seeded_repo, clock) -> None:
    with DbSession(engine) as db:
        seed(db, cfg=cfg, now_ns=clock())
        db.commit()
        user = db.get(User, "author@maha")
    assert user is not None and verify_password(user.password_hash, cfg.demo_password)


def test_reset_state_returns_to_a_fresh_seed(engine, cfg, seeded_repo: Path, clock) -> None:
    reset_state(engine, cfg, clock())
    fresh = snapshot(engine)
    with DbSession(engine) as db:
        db.add(
            Source(
                id="src_extra", tenant_id="t_maha_power", name="x", vendor="custom", zone="dmz",
                transport="http_push", created_at="x",
            )
        )  # fmt: skip
        db.commit()
    (seeded_repo / "t_maha_power").mkdir()
    (seeded_repo / "t_maha_power" / "authsrv.yaml").write_text("contract: authsrv\n", "utf-8")
    commit_all(seeded_repo, "authsrv@1", author="author@maha")

    reset_state(engine, cfg, clock())
    assert snapshot(engine) == fresh
    assert not (seeded_repo / "t_maha_power" / "authsrv.yaml").exists()


def test_unknown_scenarios_are_rejected_before_anything_is_wiped(
    engine, cfg, seeded_repo, clock
) -> None:
    reset_state(engine, cfg, clock())
    before = snapshot(engine)
    with pytest.raises(ValueError, match="unknown scenario 'nope'"):
        reset_state(engine, cfg, clock(), scenario="nope")
    assert snapshot(engine) == before


def test_seed_cli_without_kafka(cfg, seeded_repo, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--no-publish"], cfg=cfg) == 0
    assert json.loads(capsys.readouterr().out) == {
        "tenants": 2,
        "users": 3,
        "sources": 2,
        "contracts": 2,
    }
    assert len(cfg.inventory_file.read_text(encoding="utf-8").splitlines()) == 3
