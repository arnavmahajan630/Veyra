"""Idempotent ClickHouse migrations (IF-CH-SCHEMA).

Files are ``packages/veyra_lineage/migrations/NNN_<name>.sql``. Each one is applied once,
in version order, and recorded in ``<db>.schema_migrations``. Every statement in them is
``IF NOT EXISTS``, so a file interrupted half-way is safe to run again.

The lineage indexer calls :func:`migrate` at startup; tools call it too::

    python -m veyra_lineage.migrate            # apply pending migrations, print status
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from clickhouse_connect.driver.client import Client

from veyra_common.settings import Settings, settings
from veyra_lineage.client import make_client

log = logging.getLogger(__name__)

_FILE_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
_TTL_RE = re.compile(r"TTL (.+?) \+ toIntervalDay\((\d+)\)")
_DB_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def migrations_dir() -> Path:
    """``packages/veyra_lineage/migrations`` (editable install, which every image uses)."""
    return Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()

    def statements(self, *, db: str, ttl_days: int) -> list[str]:
        """The file's statements with ``{db}``/``{ttl_days}`` filled in.

        Plain ``str.replace``, not ``format``: SQL is full of braces.
        """
        body = "\n".join(
            line for line in self.sql.splitlines() if not line.lstrip().startswith("--")
        )
        body = body.replace("{db}", db).replace("{ttl_days}", str(int(ttl_days)))
        return [stmt.strip() for stmt in body.split(";") if stmt.strip()]


def load_migrations(directory: Path | None = None) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted((directory or migrations_dir()).glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise ValueError(f"bad migration file name: {path.name} (want NNN_name.sql)")
        found.append(Migration(int(match.group(1)), match.group(2), path.read_text()))
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration versions: {versions}")
    return found


def _check_db(db: str) -> str:
    if not _DB_RE.match(db):
        raise ValueError(f"unsafe ClickHouse database name: {db!r}")
    return db


def applied_versions(client: Client, db: str) -> dict[int, str]:
    rows = client.query(
        f"SELECT version, argMax(checksum, applied_at) FROM {db}.schema_migrations GROUP BY version"
    ).result_rows
    return {int(v): str(c) for v, c in rows}


def migrate(
    cfg: Settings | None = None,
    *,
    client: Client | None = None,
    db: str | None = None,
    directory: Path | None = None,
) -> list[str]:
    """Create the database, apply pending migrations, reconcile TTL. Returns applied names."""
    s = cfg or settings
    db = _check_db(db or s.clickhouse_db)
    ch = client or make_client(s, database="")
    ch.command(f"CREATE DATABASE IF NOT EXISTS {db}")
    ch.command(
        f"CREATE TABLE IF NOT EXISTS {db}.schema_migrations ("
        " version UInt32, name String, checksum String,"
        " applied_at DateTime64(3, 'UTC') DEFAULT now64(3)"
        ") ENGINE = ReplacingMergeTree(applied_at) ORDER BY version"
    )
    done = applied_versions(ch, db)
    applied: list[str] = []
    for mig in load_migrations(directory):
        if mig.version in done:
            if done[mig.version] != mig.checksum:
                # Never re-run an edited migration: add a new file instead.
                log.warning(
                    "applied migration was edited since",
                    extra={"migration": f"{mig.version:03d}_{mig.name}"},
                )
            continue
        for stmt in mig.statements(db=db, ttl_days=s.lineage_ttl_days):
            ch.command(stmt)
        ch.insert(
            f"{db}.schema_migrations",
            [[mig.version, mig.name, mig.checksum]],
            column_names=["version", "name", "checksum"],
        )
        applied.append(f"{mig.version:03d}_{mig.name}")
        log.info("migration applied", extra={"migration": applied[-1], "db": db})
    ensure_ttl(ch, db, s.lineage_ttl_days)
    return applied


def ensure_ttl(client: Client, db: str, ttl_days: int) -> list[str]:
    """Make every table's TTL match ``VEYRA_LINEAGE_TTL_DAYS``. Returns the tables changed.

    Existing parts are not rewritten (``materialize_ttl_after_modify=0``); the new TTL is
    enforced as parts merge, which is what a knob change on a live system wants.
    """
    changed: list[str] = []
    rows = client.query(
        "SELECT name, engine_full FROM system.tables "
        "WHERE database = {db:String} AND engine LIKE '%MergeTree'",
        parameters={"db": db},
    ).result_rows
    for name, engine_full in rows:
        match = _TTL_RE.search(engine_full)
        if not match or int(match.group(2)) == ttl_days:
            continue
        client.command(
            f"ALTER TABLE {db}.{name} MODIFY TTL {match.group(1)} + toIntervalDay({int(ttl_days)})",
            settings={"materialize_ttl_after_modify": 0},
        )
        changed.append(name)
        log.info("ttl updated", extra={"table": name, "ttl_days": ttl_days})
    return changed


def main() -> None:
    """``python -m veyra_lineage.migrate``."""
    from veyra_common.logging import setup_logging

    setup_logging("veyra_lineage.migrate", settings.log_level)
    applied = migrate()
    print(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")


if __name__ == "__main__":
    main()
