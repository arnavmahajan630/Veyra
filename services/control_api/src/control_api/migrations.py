"""A tiny forward-only migration list (C1: "Alembic-free").

``SQLModel.metadata.create_all`` builds the current schema, so a fresh database is
stamped at the latest version. Each entry changes tables that already exist in an older
database; new tables need no entry, because ``create_all`` adds missing tables. One SQL
statement per entry. Append only; never edit or reorder an entry.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

MIGRATIONS: list[tuple[int, str]] = [
    # C2: contract lifecycle bookkeeping
    (1, "ALTER TABLE contract_versions ADD COLUMN lint_json VARCHAR"),
    (2, "ALTER TABLE contract_versions ADD COLUMN approved_at VARCHAR"),
    (3, "ALTER TABLE contract_versions ADD COLUMN promoted_by VARCHAR"),
    (4, "ALTER TABLE contract_versions ADD COLUMN promoted_at VARCHAR"),
    (5, "ALTER TABLE contract_versions ADD COLUMN retired_reason VARCHAR"),
    # C2: replay jobs
    (6, "ALTER TABLE replay_jobs ADD COLUMN created_by VARCHAR"),
    (7, "ALTER TABLE replay_jobs ADD COLUMN finished_at VARCHAR"),
    (8, "ALTER TABLE replay_jobs ADD COLUMN detail VARCHAR NOT NULL DEFAULT ''"),
    # C3: drift inbox
    (9, "ALTER TABLE drift_items ADD COLUMN sample_event_uids_json VARCHAR NOT NULL DEFAULT '[]'"),
    (10, "ALTER TABLE drift_items ADD COLUMN resolved_by VARCHAR"),
    (11, "ALTER TABLE drift_items ADD COLUMN updated_at VARCHAR"),
    # C4: drafts
    (12, "ALTER TABLE drafts ADD COLUMN contract_id VARCHAR"),
    (13, "ALTER TABLE drafts ADD COLUMN created_by VARCHAR"),
    (14, "ALTER TABLE drafts ADD COLUMN updated_at VARCHAR"),
    (15, "ALTER TABLE drafts ADD COLUMN detail VARCHAR NOT NULL DEFAULT ''"),
]


def latest_version() -> int:
    return max((number for number, _ in MIGRATIONS), default=0)


def current_version(engine: Engine) -> int:
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


def stamp(engine: Engine, version: int) -> None:
    current_version(engine)  # creates the table
    if version:
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO schema_version (version) VALUES (?)", (version,))


def apply_migrations(engine: Engine) -> int:
    version = current_version(engine)
    with engine.begin() as conn:
        for number, sql in MIGRATIONS:
            if number > version:
                conn.exec_driver_sql(sql)
                conn.exec_driver_sql("INSERT INTO schema_version (version) VALUES (?)", (number,))
                version = number
    return version
