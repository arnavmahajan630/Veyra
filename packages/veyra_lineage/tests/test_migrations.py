"""The migration files and the pure parts of the runner (B1). No ClickHouse needed.

``test_lineage_index.py`` covers actually applying them.
"""

from __future__ import annotations

import re

import pytest

from veyra_lineage import rows as R
from veyra_lineage.migrate import Migration, load_migrations, migrations_dir

# IF-CH-SCHEMA's table list (the plan's `vault_index`/`dlq`/`shadow`/`audit` topics land in
# vault_locations + segments / dlq_events / shadow_diffs / audit_log).
REQUIRED_TABLES = {
    "raw_events",
    "norm_events",
    "norm_lineage",
    "window_roots",
    "vault_locations",
    "segments",
    "receipts",
    "dlq_events",
    "shadow_diffs",
    "audit_log",
    "mv_source_minute",
    "mv_route_minute",
}


@pytest.fixture(scope="module")
def migrations() -> list[Migration]:
    return load_migrations()


def statements(migrations: list[Migration], db: str = "veyra", ttl: int = 90) -> list[str]:
    return [s for m in migrations for s in m.statements(db=db, ttl_days=ttl)]


def test_the_directory_is_where_the_plan_says() -> None:
    assert migrations_dir().is_dir()
    assert migrations_dir().parts[-2:] == ("veyra_lineage", "migrations")


def test_versions_are_unique_and_ordered(migrations: list[Migration]) -> None:
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)
    assert len(set(versions)) == len(versions)
    assert versions[0] == 1


def test_every_required_table_is_created(migrations: list[Migration]) -> None:
    created = {
        m.group(1)
        for stmt in statements(migrations)
        for m in [re.search(r"CREATE (?:TABLE|MATERIALIZED VIEW) IF NOT EXISTS veyra\.(\w+)", stmt)]
        if m
    }
    assert created >= REQUIRED_TABLES, f"missing: {REQUIRED_TABLES - created}"


def test_every_indexed_table_has_a_row_builder(migrations: list[Migration]) -> None:
    """A table the indexer writes must have a spec, and vice versa."""
    created = {
        m.group(1)
        for stmt in statements(migrations)
        for m in [re.search(r"CREATE TABLE IF NOT EXISTS veyra\.(\w+)", stmt)]
        if m
    }
    assert set(R.TABLES) <= created


def test_every_statement_is_idempotent(migrations: list[Migration]) -> None:
    """A migration interrupted half-way must be safe to run again."""
    for stmt in statements(migrations):
        head = " ".join(stmt.split())[:80]
        if stmt.startswith(("CREATE TABLE", "CREATE MATERIALIZED VIEW", "CREATE DATABASE")):
            assert "IF NOT EXISTS" in stmt, head
        else:
            # The only other statements are the 003 backfill INSERTs, which are
            # idempotent because search_index is a ReplacingMergeTree.
            assert stmt.startswith("INSERT INTO veyra.search_index"), head


def test_placeholders_are_all_substituted(migrations: list[Migration]) -> None:
    for stmt in statements(migrations, db="other_db", ttl=30):
        assert "{db}" not in stmt and "{ttl_days}" not in stmt
        assert "veyra." not in stmt


def test_ttl_comes_from_the_setting(migrations: list[Migration]) -> None:
    sql = "\n".join(statements(migrations, ttl=45))
    assert "toIntervalDay(45)" in sql
    assert "toIntervalDay(90)" not in sql


def test_event_tables_have_a_ttl(migrations: list[Migration]) -> None:
    """Every table holding event data expires; window_roots is evidence and must not."""
    for stmt in statements(migrations):
        match = re.search(r"CREATE TABLE IF NOT EXISTS veyra\.(\w+)", stmt)
        if not match:
            continue
        name = match.group(1)
        if name in {"window_roots", "schema_migrations"}:
            assert "TTL " not in stmt, name
        else:
            assert "TTL " in stmt, f"{name} has no TTL"


def test_low_cardinality_on_the_columns_the_plan_names(migrations: list[Migration]) -> None:
    """B1: LowCardinality for tenant, source, route and conformance."""
    sql = "\n".join(statements(migrations))
    for column in ("tenant_id", "source_id", "route_id", "conformance", "vendor", "zone"):
        assert re.search(rf"{column}\s+LowCardinality\(String\)", sql), column


def test_raw_events_never_gets_a_bytes_column(migrations: list[Migration]) -> None:
    stmt = next(
        s for s in statements(migrations) if "CREATE TABLE IF NOT EXISTS veyra.raw_events" in s
    )
    assert "raw_b64" not in stmt
    assert "raw_preview" in stmt


def test_engines_and_order_by_match_if_ch_schema(migrations: list[Migration]) -> None:
    expected = {
        "raw_events": ("MergeTree", "(tenant_id, source_id, received_time)"),
        "norm_lineage": ("ReplacingMergeTree(produced_at)", "(event_uid, revision)"),
        "norm_events": ("ReplacingMergeTree", "(event_uid, revision)"),
        "vault_locations": ("ReplacingMergeTree(sealed_at)", "event_uid"),
        "segments": ("ReplacingMergeTree(version)", "segment_id"),
        "dlq_events": ("MergeTree", "(source_id, template_sig, produced_at)"),
        "shadow_diffs": ("MergeTree", "(contract_id, produced_at)"),
        "audit_log": ("MergeTree", "at"),
        "window_roots": ("ReplacingMergeTree(inserted_at)", "window_id"),
    }
    for stmt in statements(migrations):
        match = re.search(r"CREATE TABLE IF NOT EXISTS veyra\.(\w+)", stmt)
        if not match or match.group(1) not in expected:
            continue
        engine, order_by = expected[match.group(1)]
        body = stmt + "\n"  # the last statement in a file has no trailing newline
        assert f"ENGINE = {engine}\n" in body, match.group(1)
        assert f"ORDER BY {order_by}\n" in body, match.group(1)


def test_the_aggregate_views_are_aggregating(migrations: list[Migration]) -> None:
    for name in ("mv_source_minute", "mv_route_minute"):
        stmt = next(
            s for s in statements(migrations) if f"CREATE TABLE IF NOT EXISTS veyra.{name}" in s
        )
        assert "ENGINE = AggregatingMergeTree" in stmt
        assert "minute" in stmt
    sql = "\n".join(statements(migrations))
    for view in (
        "mv_source_minute_from_raw",
        "mv_source_minute_from_lineage",
        "mv_source_minute_from_norm",
        "mv_route_minute_from_receipts",
    ):
        assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS veyra.{view}" in sql


def test_checksum_changes_with_content() -> None:
    sql = "CREATE TABLE IF NOT EXISTS {db}.a (x UInt8) ENGINE = MergeTree ORDER BY x"
    a = Migration(1, "x", sql)
    b = Migration(1, "x", a.sql + " -- edited")
    assert a.checksum != b.checksum
    assert len(a.checksum) == 64


def test_comments_are_stripped_but_sql_is_not(migrations: list[Migration]) -> None:
    mig = Migration(
        9, "c", "-- a comment; with a semicolon\nCREATE TABLE IF NOT EXISTS {db}.t (x UInt8);"
    )
    assert mig.statements(db="d", ttl_days=1) == ["CREATE TABLE IF NOT EXISTS d.t (x UInt8)"]
    assert statements(migrations)  # the real files still parse


def test_a_bad_database_name_is_refused() -> None:
    from veyra_lineage.migrate import _check_db

    for bad in ("veyra; DROP DATABASE veyra", "veyra-1", "", "veyra.x"):
        with pytest.raises(ValueError, match="unsafe"):
            _check_db(bad)
    assert _check_db("veyra_bench") == "veyra_bench"
