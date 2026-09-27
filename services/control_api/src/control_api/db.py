"""The SQLite engine (WAL, foreign keys on) and schema lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from control_api import migrations
from control_api import tables as _tables  # registers every table on SQLModel.metadata

_ = _tables


def make_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False}
    )
    event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def init_db(engine: Engine) -> None:
    fresh = not inspect(engine).has_table("tenants")
    SQLModel.metadata.create_all(engine)
    if fresh:
        migrations.stamp(engine, migrations.latest_version())
    else:
        migrations.apply_migrations(engine)


def reset_db(engine: Engine) -> None:
    SQLModel.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS schema_version")
    init_db(engine)
