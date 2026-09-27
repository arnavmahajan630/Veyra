"""A tiny forward-only migration list (C1: "Alembic-free").

``SQLModel.metadata.create_all`` builds the current schema, so a fresh database is
stamped at the latest version. Each entry changes tables that already exist in an older
database. Append only; never edit or reorder an entry.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

MIGRATIONS: list[tuple[int, str]] = []


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
