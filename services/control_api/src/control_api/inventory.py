"""IF-INVENTORY writer: edge/vector/inventory/sources.csv plus the reload stamp.

The CSV is replaced atomically (tmp + rename in the same directory). The stamp is written
**in place**, because it may be a single-file bind mount, which can't be renamed over.
Its last line is a counter, so every rewrite is a real content change for the watcher.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Iterable
from pathlib import Path

from sqlmodel import Session as DbSession
from sqlmodel import select

from control_api.tables import Source
from veyra_common.settings import Settings

HEADER = ("listener", "match_kind", "match_value", "source_id", "tenant_id", "vendor", "zone")
SYSLOG_TRANSPORTS = frozenset({"syslog_udp", "syslog_tcp"})


def inventory_rows(sources: Iterable[Source]) -> list[tuple[str, ...]]:
    rows = [
        (
            str(s.listener),
            str(s.match_kind),
            str(s.match_value),
            s.id,
            s.tenant_id,
            s.vendor,
            s.zone,
        )
        for s in sources
        if s.status == "active"
        and s.transport in SYSLOG_TRANSPORTS
        and s.listener
        and s.match_kind
        and s.match_value
    ]
    return sorted(rows)


def write_inventory(path: Path, stamp: Path, rows: Iterable[tuple[str, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)
    os.replace(tmp, path)
    bump_stamp(stamp)


def bump_stamp(stamp: Path) -> int:
    lines = stamp.read_text(encoding="utf-8").splitlines() if stamp.exists() else []
    if lines and lines[-1].strip().isdigit():
        counter = int(lines[-1]) + 1
        lines[-1] = str(counter)
    else:
        counter = 1
        lines.append(str(counter))
    stamp.parent.mkdir(parents=True, exist_ok=True)
    with stamp.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return counter


def rewrite_inventory(db: DbSession, cfg: Settings) -> int:
    rows = inventory_rows(db.exec(select(Source)).all())
    write_inventory(cfg.inventory_file, cfg.inventory_reload_stamp, rows)
    return len(rows)
