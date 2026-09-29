"""IF-SIGNED-ROOT — the signed, chained window roots and the append-only ledger.

Each line of ``data/vault/roots/ledger.ndjson`` is ``{"payload": {...}, "sig_b64": "..."}``:

* ``payload`` is an IF-SIGNED-ROOT document (``veyra_common.models.SignedRoot``), written
  as **canonical JSON** — sorted keys, no whitespace, UTF-8 — because that exact byte
  string is what gets signed.
* ``prev_signed_sha256`` is ``SHA256`` of the *previous* entry's canonical payload, so the
  entries form a chain: editing an old root breaks every root after it.

The genesis entry's ``prev_signed_sha256`` is 64 zeros.

This module holds the format only, so the writer (the integrity service) and the reader
(``tools/ledger_audit.py``, the evidence API) cannot drift apart.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from veyra_common.models import SignedRoot

GENESIS_PREV = "0" * 64
LEDGER_NAME = "ledger.ndjson"


class LedgerError(Exception):
    """The ledger file is malformed."""


def canonical_bytes(payload: SignedRoot | dict[str, Any]) -> bytes:
    """The exact bytes that are signed and hashed: sorted keys, no whitespace, UTF-8."""
    data = payload.model_dump(mode="json") if isinstance(payload, SignedRoot) else payload
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def payload_sha256(payload: SignedRoot | dict[str, Any]) -> str:
    """``prev_signed_sha256`` for the entry that follows this one."""
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def window_id(window_start: int) -> str:
    """IF-NAMING: ``w_<unix_start>``."""
    return f"w_{int(window_start)}"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One ledger line: the signed payload plus its signature."""

    payload: dict[str, Any]
    sig_b64: str

    @property
    def signature(self) -> bytes:
        return base64.b64decode(self.sig_b64)

    @property
    def window_id(self) -> str:
        return str(self.payload["window_id"])

    @property
    def prev_signed_sha256(self) -> str:
        return str(self.payload["prev_signed_sha256"])

    @property
    def canonical(self) -> bytes:
        return canonical_bytes(self.payload)

    @property
    def sha256(self) -> str:
        return payload_sha256(self.payload)

    def as_root(self) -> SignedRoot:
        """Validate the payload against IF-SIGNED-ROOT."""
        return SignedRoot.model_validate(self.payload)

    def to_line(self) -> str:
        return json.dumps(
            {"payload": self.payload, "sig_b64": self.sig_b64},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )


def ledger_path(cfg_or_dir: Any) -> Path:
    """``<vault_dir>/roots/ledger.ndjson`` for a Settings, or a directory as given."""
    if isinstance(cfg_or_dir, Path):
        return cfg_or_dir / LEDGER_NAME
    return Path(cfg_or_dir.vault_dir) / "roots" / LEDGER_NAME


def read_ledger(path: Path) -> list[LedgerEntry]:
    """Every entry in file order. A missing file is an empty ledger, not an error."""
    return list(iter_ledger(path))


def iter_ledger(path: Path) -> Iterator[LedgerEntry]:
    if not Path(path).exists():
        return
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                yield LedgerEntry(raw["payload"], raw["sig_b64"])
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise LedgerError(f"{path}:{number} is not a ledger entry: {exc}") from exc


def last_entry(path: Path) -> LedgerEntry | None:
    """The newest entry, which the next root chains onto."""
    entries = read_ledger(path)
    return entries[-1] if entries else None


def append_entry(path: Path, entry: LedgerEntry) -> None:
    """Append one entry durably: the ledger is evidence, so it is fsynced per line."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(entry.to_line() + "\n")
        fh.flush()
        os.fsync(fh.fileno())
