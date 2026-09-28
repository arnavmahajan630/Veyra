"""C2's schema: new lifecycle columns reach databases created by C1, and C2's knobs exist."""

from __future__ import annotations

from pathlib import Path

import pytest
from control_api import migrations
from control_api.db import init_db, make_engine
from sqlalchemy import inspect

from veyra_common.settings import Settings

PROFILES = Path(__file__).resolve().parents[3] / "profiles"
C2_KEYS = [
    "VEYRA_BACKTEST_MAX",
    "VEYRA_REPLAY_MAX",
    "VEYRA_REPLAY_TIMEOUT_S",
    "VEYRA_REPLAY_POLL_MS",
    "VEYRA_EVIDENCE_API_URL",
    "VEYRA_EVIDENCE_TIMEOUT_S",
]
# (table, column) for every "ALTER TABLE <table> ADD COLUMN <column> ..." migration
ADDED = [(sql.split()[2], sql.split()[5]) for _, sql in migrations.MIGRATIONS]


def test_the_migrations_add_the_c2_columns() -> None:
    assert {("contract_versions", "lint_json"), ("contract_versions", "retired_reason"),
            ("replay_jobs", "created_by"), ("replay_jobs", "detail")} <= set(ADDED)  # fmt: skip


def test_a_c1_database_gains_every_migrated_column(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "c1.db")
    init_db(engine)
    # Rebuild what a C1-era database looks like: no migrated columns, no C2 table,
    # schema version 0. Derived from MIGRATIONS, so later plans' migrations are covered too.
    with engine.begin() as conn:
        for table, column in ADDED:
            conn.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.exec_driver_sql("DROP TABLE contract_transitions")
        conn.exec_driver_sql("DELETE FROM schema_version")

    init_db(engine)

    for table, column in ADDED:
        assert column in {c["name"] for c in inspect(engine).get_columns(table)}, (table, column)
    assert inspect(engine).has_table("contract_transitions")
    assert migrations.current_version(engine) == migrations.latest_version()
    engine.dispose()


def test_registry_knobs_have_laptop_defaults() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert (s.backtest_max, s.replay_max, s.replay_timeout_s) == (200, 10000, 60)
    assert s.replay_poll_ms == 500
    assert s.evidence_api_url == "http://evidence-api:8100"
    assert s.evidence_timeout_s == 5.0


@pytest.mark.parametrize("profile", ["laptop", "mac", "workstation"])
def test_every_profile_sets_the_registry_knobs(profile: str) -> None:
    lines = (PROFILES / f"{profile}.env").read_text(encoding="utf-8").splitlines()
    keys = {line.split("=", 1)[0] for line in lines if "=" in line and not line.startswith("#")}
    assert set(C2_KEYS) <= keys
