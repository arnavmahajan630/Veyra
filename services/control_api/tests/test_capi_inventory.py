"""IF-INVENTORY: sources.csv is rewritten atomically and the reload stamp bumped."""

from __future__ import annotations

from pathlib import Path

from control_api.inventory import inventory_rows, write_inventory
from control_api.tables import Source

HEADER = "listener,match_kind,match_value,source_id,tenant_id,vendor,zone"


def src(**overrides: object) -> Source:
    fields: dict[str, object] = {
        "id": "src_a", "tenant_id": "t_a", "name": "A", "vendor": "linux", "zone": "core",
        "transport": "syslog_udp", "listener": "core-udp", "match_kind": "syslog_host",
        "match_value": "core-lnx-07", "created_at": "x",
    }  # fmt: skip
    fields.update(overrides)
    return Source.model_validate(fields)


def test_rows_are_the_active_syslog_sources_sorted() -> None:
    rows = inventory_rows(
        [
            src(id="src_b", listener="dmz-tcp", match_value="fw-dmz-01", zone="dmz"),
            src(id="src_http", transport="http_push", listener=None),
            src(id="src_paused", status="paused"),
            src(id="src_a"),
        ]
    )
    assert rows == [
        ("core-udp", "syslog_host", "core-lnx-07", "src_a", "t_a", "linux", "core"),
        ("dmz-tcp", "syslog_host", "fw-dmz-01", "src_b", "t_a", "linux", "dmz"),
    ]


def test_write_replaces_the_file_atomically_and_bumps_the_stamp(tmp_path: Path) -> None:
    path = tmp_path / "inventory" / "sources.csv"
    stamp = tmp_path / "reload.stamp"
    stamp.write_text("# touched by control-api\n0\n", encoding="utf-8")
    row = ("core-udp", "syslog_host", "h", "src_a", "t_a", "linux", "core")

    write_inventory(path, stamp, [row])
    assert path.read_text(encoding="utf-8") == f"{HEADER}\n{','.join(row)}\n"
    assert not path.with_name("sources.csv.tmp").exists()
    assert stamp.read_text(encoding="utf-8") == "# touched by control-api\n1\n"

    write_inventory(path, stamp, [])
    assert path.read_text(encoding="utf-8") == f"{HEADER}\n"
    assert stamp.read_text(encoding="utf-8") == "# touched by control-api\n2\n"


def test_a_missing_stamp_is_created(tmp_path: Path) -> None:
    stamp = tmp_path / "edge" / "reload.stamp"
    write_inventory(tmp_path / "sources.csv", stamp, [])
    assert stamp.read_text(encoding="utf-8") == "1\n"
