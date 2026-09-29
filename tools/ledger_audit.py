"""Audit the signed-root ledger: signatures, the prev-hash chain, PASS/FAIL (B3).

    python tools/ledger_audit.py                       # data/vault/roots/ledger.ndjson
    python tools/ledger_audit.py --ledger path.ndjson --pubkey root.pem
    python tools/ledger_audit.py --json               # machine-readable

The auditor needs only the ledger and the **public** key. Every entry is checked for:

* a valid Ed25519 signature over its canonical payload;
* ``prev_signed_sha256`` equal to the SHA-256 of the previous entry's canonical payload
  (64 zeros for the first entry);
* a payload that still satisfies IF-SIGNED-ROOT, and a window that follows the last one.

Editing any old entry therefore fails both its own signature and the chain link of every
entry after it. Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from veyra_common.settings import settings
from veyra_evidence.keys import KeyError_, get_key_provider, verify_signature
from veyra_evidence.ledger import GENESIS_PREV, LedgerEntry, LedgerError, ledger_path, read_ledger

PASS, FAIL = "PASS", "FAIL"


@dataclass
class Finding:
    entry: int
    window_id: str
    check: str
    status: str
    detail: str = ""

    def as_json(self) -> dict[str, object]:
        return {
            "entry": self.entry,
            "window_id": self.window_id,
            "check": self.check,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Report:
    ledger: str
    entries: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if f.status == FAIL]

    @property
    def status(self) -> str:
        return FAIL if self.failures else PASS

    def add(self, entry: int, window: str, check: str, ok: bool, detail: str = "") -> None:
        self.findings.append(Finding(entry, window, check, PASS if ok else FAIL, detail))

    def as_json(self) -> dict[str, object]:
        return {
            "ledger": self.ledger,
            "status": self.status,
            "entries": self.entries,
            "failures": len(self.failures),
            "findings": [f.as_json() for f in self.findings],
        }


def audit(entries: list[LedgerEntry], public_key_pem: str, ledger: str) -> Report:
    """Check every entry. One report line per check, so a FAIL says exactly what broke."""
    report = Report(ledger=ledger, entries=len(entries))
    expected_prev = GENESIS_PREV
    previous_end: int | None = None

    for index, entry in enumerate(entries):
        window = entry.window_id if "window_id" in entry.payload else f"<entry {index}>"

        # 1. The payload must still be a valid IF-SIGNED-ROOT document.
        try:
            root = entry.as_root()
            report.add(index, window, "payload_schema", True)
        except Exception as exc:
            report.add(index, window, "payload_schema", False, str(exc)[:200])
            expected_prev = entry.sha256
            continue

        # 2. The signature covers the canonical payload bytes.
        try:
            ok = verify_signature(public_key_pem, entry.canonical, entry.signature)
            report.add(index, window, "signature", ok, "" if ok else "Ed25519 verification failed")
        except (KeyError_, ValueError) as exc:
            report.add(index, window, "signature", False, str(exc)[:200])

        # 3. The chain link back to the previous entry.
        report.add(
            index,
            window,
            "prev_signed_sha256",
            entry.prev_signed_sha256 == expected_prev,
            ""
            if entry.prev_signed_sha256 == expected_prev
            else f"expected {expected_prev}, found {entry.prev_signed_sha256}",
        )

        # 4. Windows must move forward, so a root cannot be quietly reordered or replayed.
        if previous_end is not None:
            report.add(
                index,
                window,
                "window_order",
                root.window_start >= previous_end,
                ""
                if root.window_start >= previous_end
                else f"window_start {root.window_start} precedes the previous end {previous_end}",
            )
        previous_end = root.window_end
        expected_prev = entry.sha256

    return report


def load_public_key(explicit: Path | None) -> str:
    """The PEM to verify with: a given file, else the local provider's public key."""
    if explicit is not None:
        return Path(explicit).read_text()
    provider = get_key_provider()
    return provider.public_key_pem(provider.signing_key_id)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--ledger", type=Path, help="ledger file (default: the vault's)")
    parser.add_argument("--pubkey", type=Path, help="Ed25519 public key PEM to verify with")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    path = args.ledger or ledger_path(settings)
    try:
        entries = read_ledger(Path(path))
    except LedgerError as exc:
        print(f"{FAIL}: {exc}", file=sys.stderr)
        return 1
    try:
        public_key_pem = load_public_key(args.pubkey)
    except (OSError, KeyError_) as exc:
        print(f"{FAIL}: cannot load the public key: {exc}", file=sys.stderr)
        return 1

    report = audit(entries, public_key_pem, str(path))

    if args.as_json:
        print(json.dumps(report.as_json(), indent=2))
    else:
        print(f"ledger: {path}")
        if not entries:
            print("no entries yet (nothing to verify)")
        for finding in report.findings:
            if finding.status == FAIL:
                print(f"  {FAIL} entry {finding.entry} {finding.window_id}: {finding.check}")
                if finding.detail:
                    print(f"       {finding.detail}")
        checks = len(report.findings)
        print(
            f"{report.status}: {report.entries} entries, {checks} checks, "
            f"{len(report.failures)} failed"
        )
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
