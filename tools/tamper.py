"""Tamper lab (B5 prototype): break sealed evidence, watch B4 catch it, put it back.

    python tools/tamper.py naive_flip      --event <uid>
    python tools/tamper.py insider_rewrite --event <uid>
    python tools/tamper.py segment_delete  --event <uid>
    python tools/tamper.py root_rewrite    --event <uid>
    python tools/tamper.py untamper        --event <uid>
    python tools/tamper.py verify          --event <uid>     # just show the report
    python tools/tamper.py list                              # what is tampered now

Every mode copies the files it touches into ``data/tamper_backup/`` **before** changing
anything and records them in ``manifest.json``, so ``untamper`` restores the vault
byte-for-byte. Each mode prints the B4 verification report afterwards, so the demo shows
which of the eight checks noticed.

The modes differ in what the attacker is assumed to hold:

=================  ==========================================  =====================
mode               attacker capability                          first failing check
=================  ==========================================  =====================
naive_flip         write access to the vault file               fetch_raw (GCM)
insider_rewrite    the KEK too, so they re-encrypt cleanly      merkle_inclusion
segment_delete      write access; deletes the evidence          fetch_raw
root_rewrite       write access to the ledger                   root_signature
=================  ==========================================  =====================

See ``docs/tamper_matrix.md``. Nothing here is a security feature — it is a demo of why
the signed Merkle root has to live outside the thing it protects.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence_api.locator import EvidenceNotFound, Located, VaultLocator
from evidence_api.verifier import Verifier

from veyra_common.hashing import sha256_hex
from veyra_common.logging import setup_logging
from veyra_common.settings import Settings, settings
from veyra_evidence.keys import LocalKeyProvider, get_key_provider
from veyra_evidence.ledger import ledger_path
from veyra_evidence.segment import SegmentWriter

BACKUP_DIR = "tamper_backup"
MANIFEST = "manifest.json"
MODES = ("naive_flip", "insider_rewrite", "segment_delete", "root_rewrite")


class TamperError(Exception):
    """The requested tampering could not be applied."""


@dataclass(frozen=True, slots=True)
class Lab:
    cfg: Settings
    keys: LocalKeyProvider
    locator: VaultLocator
    verifier: Verifier

    @property
    def backup_dir(self) -> Path:
        return Path(self.cfg.data_dir) / BACKUP_DIR

    @property
    def manifest_path(self) -> Path:
        return self.backup_dir / MANIFEST


def open_lab(cfg: Settings | None = None) -> Lab:
    s = cfg or settings
    keys = get_key_provider(s)
    locator = VaultLocator(s, keys)
    return Lab(s, keys, locator, Verifier(locator))


# ---------------------------------------------------------------- backups
def _read_manifest(lab: Lab) -> list[dict[str, Any]]:
    if not lab.manifest_path.exists():
        return []
    return list(json.loads(lab.manifest_path.read_text()))


def _write_manifest(lab: Lab, records: list[dict[str, Any]]) -> None:
    lab.backup_dir.mkdir(parents=True, exist_ok=True)
    lab.manifest_path.write_text(json.dumps(records, indent=2) + "\n")


def back_up(lab: Lab, mode: str, event_uid: str, paths: list[Path]) -> dict[str, Any]:
    """Copy every file a mode is about to touch, and record it. Runs before any change."""
    entry: dict[str, Any] = {
        "mode": mode,
        "event_uid": event_uid,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": [],
    }
    stamp = str(int(time.time() * 1000))
    for path in paths:
        copy = lab.backup_dir / stamp / path.name
        copy.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        if existed:
            shutil.copy2(path, copy)
        entry["files"].append(
            {"path": str(path), "backup": str(copy) if existed else None, "existed": existed}
        )
    records = _read_manifest(lab)
    records.append(entry)
    _write_manifest(lab, records)
    return entry


def _make_writable(path: Path) -> None:
    """Sealed segments are 0444; an attacker with write access would chmod first."""
    if path.exists():
        os.chmod(path, 0o644)


# ---------------------------------------------------------------- modes
def naive_flip(lab: Lab, located: Located) -> str:
    """Flip one bit of the ciphertext. The crudest attack, and the easiest to catch."""
    path = located.path
    back_up(lab, "naive_flip", located.event_uid, [path])
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01  # inside the GCM tag / ciphertext tail
    _make_writable(path)
    path.write_bytes(bytes(blob))
    os.chmod(path, 0o444)
    return f"flipped the last byte of {path.name}"


def insider_rewrite(lab: Lab, located: Located) -> str:
    """Rewrite the payload **and** re-seal the segment so it is internally consistent.

    This is the interesting one: the attacker holds the KEK, so they can decrypt,
    edit, re-encrypt, and fix up every hash *inside* the segment. What they cannot do is
    re-sign the window root, so the segment's digest no longer matches the leaf the
    signed root commits to — ``merkle_inclusion`` fails.
    """
    path = located.path
    header = located.header
    back_up(lab, "insider_rewrite", located.event_uid, [path])

    reader = lab.locator.reader(path)
    records = list(reader.records())
    forged_note = b" [ALTERED BY AN INSIDER]"

    writer = SegmentWriter(
        str(header["topic"]),
        int(header["partition"]),
        int(header["first_offset"]),
        prev_chain_hash=bytes.fromhex(str(header["prev_chain_hash_hex"])),
        key_provider=lab.keys,
        chain_epoch=int(header.get("chain_epoch", 0)),
        vault_dir=lab.locator.vault_dir,
        cfg=lab.cfg,
    )
    for record in records:
        envelope = json.loads(record.envelope_bytes)
        if envelope["event_uid"] == located.event_uid:
            raw = base64.b64decode(envelope["raw_b64"]) + forged_note
            envelope["raw_b64"] = base64.b64encode(raw).decode()
            envelope["raw_len"] = len(raw)
            # Keep the envelope self-consistent: recompute its own hash too.
            envelope["raw_sha256"] = sha256_hex(raw)
        payload = json.dumps(envelope, separators=(",", ":")).encode()
        writer.append(record.offset, payload, envelope["raw_sha256"], envelope["event_uid"])

    _make_writable(path)
    sealed = writer.seal()  # rewrites the same segment_id path, fully re-encrypted
    return (
        f"re-sealed {sealed.path.name} with an altered payload; "
        f"digest {located.digest_hex[:16]}... -> {sealed.digest_hex[:16]}..."
    )


def segment_delete(lab: Lab, located: Located) -> str:
    """Delete the evidence outright. Absence has to be as detectable as alteration."""
    path = located.path
    back_up(lab, "segment_delete", located.event_uid, [path])
    _make_writable(path)
    path.unlink()
    return f"deleted {path.name}"


def root_rewrite(lab: Lab, located: Located) -> str:
    """Rewrite the signed root in the ledger. Without the private key it cannot be re-signed."""
    entry = located.ledger_entry
    if entry is None:
        raise TamperError(
            f"event {located.event_uid} is not covered by a signed root yet; "
            "run the integrity service first"
        )
    ledger = ledger_path(lab.cfg)
    back_up(lab, "root_rewrite", located.event_uid, [ledger])

    lines = ledger.read_text().splitlines()
    for index, line in enumerate(lines):
        record = json.loads(line)
        if record["payload"].get("window_id") == entry.window_id:
            record["payload"]["root"] = "0" * 64
            lines[index] = json.dumps(record, sort_keys=True, separators=(",", ":"))
            break
    else:  # pragma: no cover - the entry came from this file
        raise TamperError(f"{entry.window_id} is not in {ledger}")
    ledger.write_text("\n".join(lines) + "\n")
    return f"rewrote the root of {entry.window_id} in {ledger.name}"


TAMPER_FUNCS = {
    "naive_flip": naive_flip,
    "insider_rewrite": insider_rewrite,
    "segment_delete": segment_delete,
    "root_rewrite": root_rewrite,
}


def tamper(lab: Lab, mode: str, event_uid: str) -> dict[str, Any]:
    """Apply one mode to one event and return what happened plus the B4 verdict."""
    if mode not in TAMPER_FUNCS:
        raise TamperError(f"unknown mode {mode!r}; choose from {', '.join(MODES)}")
    try:
        located = lab.locator.locate(event_uid)
    except EvidenceNotFound as exc:
        raise TamperError(str(exc)) from exc
    detail = TAMPER_FUNCS[mode](lab, located)
    report = lab.verifier.verify(event_uid)
    return {
        "mode": mode,
        "event_uid": event_uid,
        "detail": detail,
        "segment_id": located.segment_id,
        "verified": report.verified,
        "failed_steps": [s.id for s in report.steps if not s.ok and s.status is None],
        "report": report.as_json(),
    }


# ---------------------------------------------------------------- restore
def untamper(lab: Lab, event_uid: str | None = None) -> dict[str, Any]:
    """Undo tampering, newest first. Without ``event_uid``, undoes everything."""
    records = _read_manifest(lab)
    keep: list[dict[str, Any]] = []
    restored: list[str] = []
    for entry in reversed(records):
        if event_uid is not None and entry["event_uid"] != event_uid:
            keep.append(entry)
            continue
        for item in entry["files"]:
            target = Path(item["path"])
            if item["existed"]:
                _make_writable(target)
                shutil.copy2(item["backup"], target)
                # Segments are sealed read-only; the ledger stays writable.
                if target.suffix == ".seg":
                    os.chmod(target, 0o444)
            elif target.exists():
                _make_writable(target)
                target.unlink()
            restored.append(target.name)
    _write_manifest(lab, list(reversed(keep)))
    return {"restored": restored, "operations_undone": len(records) - len(keep)}


def active(lab: Lab) -> list[dict[str, Any]]:
    return [
        {"mode": e["mode"], "event_uid": e["event_uid"], "at": e["at"]} for e in _read_manifest(lab)
    ]


# ---------------------------------------------------------------- CLI
def _print_report(report: dict[str, Any]) -> None:
    print(f"\nB4 verification: {'PASS' if report['verified'] else 'FAIL'}")
    for step in report["steps"]:
        mark = "ok  " if step["ok"] else "FAIL"
        note = f" [{step['status']}]" if step.get("status") else ""
        print(f"  {mark} {step['id']:<18}{note} {step['detail'][:72]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "command", choices=[*MODES, "untamper", "verify", "list"], help="what to do"
    )
    parser.add_argument("--event", help="event_uid to target")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    # A tampered segment makes the locator log "unreadable segment", which is expected
    # here and would only clutter the demo output; the report below says what happened.
    setup_logging("tamper", "ERROR")

    lab = open_lab()

    if args.command == "list":
        entries = active(lab)
        print(json.dumps(entries, indent=2) if args.as_json else _format_list(entries))
        return 0

    if args.command == "verify":
        if not args.event:
            parser.error("verify needs --event")
        report = lab.verifier.verify(args.event).as_json()
        print(json.dumps(report, indent=2)) if args.as_json else _print_report(report)
        return 0 if report["verified"] else 1

    if args.command == "untamper":
        result = untamper(lab, args.event)
        if args.as_json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"restored {len(result['restored'])} file(s) from "
                f"{result['operations_undone']} operation(s)"
            )
            if args.event:
                _print_report(lab.verifier.verify(args.event).as_json())
        return 0

    if not args.event:
        parser.error(f"{args.command} needs --event")
    try:
        result = tamper(lab, args.command, args.event)
    except TamperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.as_json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{result['mode']}: {result['detail']}")
    _print_report(result["report"])
    print("\ndetected by: " + (", ".join(result["failed_steps"]) or "nothing — that is a bug"))
    print(f"restore with: python tools/tamper.py untamper --event {args.event}")
    return 0


def _format_list(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "nothing is tampered (the vault is pristine)"
    return "\n".join(f"  {e['at']}  {e['mode']:<16} {e['event_uid']}" for e in entries)


if __name__ == "__main__":
    sys.exit(main())
