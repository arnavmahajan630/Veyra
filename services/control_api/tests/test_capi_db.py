"""SQLite in WAL mode, every C1 table, and the forward-only migration list."""

from __future__ import annotations

from pathlib import Path

import pytest
from control_api import migrations
from control_api.db import init_db, make_engine, reset_db
from control_api.migrations import current_version
from control_api.tables import Tenant
from sqlalchemy import inspect
from sqlmodel import Session as DbSession
from sqlmodel import select

C1_TABLES = {
    "tenants", "users", "sessions", "sources", "api_keys", "contracts", "contract_versions",
    "drafts", "drift_items", "replay_jobs", "audit", "schema_version",
}  # fmt: skip


def test_init_db_creates_every_table(engine) -> None:
    assert set(inspect(engine).get_table_names()) >= C1_TABLES


def test_sqlite_runs_in_wal_mode(engine) -> None:
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"


def test_a_fresh_database_is_stamped_at_the_latest_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, "SELECT 1"), (2, "SELECT 1")])
    engine = make_engine(tmp_path / "fresh.db")
    init_db(engine)
    assert current_version(engine) == 2
    engine.dispose()


def test_an_old_database_gets_only_newer_migrations_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path / "old.db")
    init_db(engine)
    assert current_version(engine) == 0
    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, "ALTER TABLE tenants ADD COLUMN note TEXT")])
    init_db(engine)
    init_db(engine)
    assert current_version(engine) == 1
    assert "note" in {c["name"] for c in inspect(engine).get_columns("tenants")}
    engine.dispose()


def test_reset_db_empties_every_table(engine) -> None:
    with DbSession(engine) as db:
        db.add(Tenant(id="t_x", name="X", created_at="2026-09-27T00:00:00.000000000Z"))
        db.commit()
    reset_db(engine)
    with DbSession(engine) as db:
        assert db.exec(select(Tenant)).all() == []
