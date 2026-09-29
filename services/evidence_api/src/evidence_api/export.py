"""The evidence export bundle (``POST /evidence/export/{event_uid}``).

A zip an auditor can check on their own machine, with no VEYRA install and no keys:

==========================  =================================================
raw.bin                     the exact original bytes
envelope.json               IF-ENVELOPE as archived
segment_manifest.json       the segment header plus the chain inputs per record
proof.json                  the Merkle audit path for this segment
signed_root.json            the IF-SIGNED-ROOT payload and its signature
pubkey.pem                  the Ed25519 public key
verify.py                   re-derives all of the above (see verify_template)
README.txt                  what the bundle is and how to check it
==========================  =================================================

The segment's data key is deliberately **not** included — ``wrapped_dek_b64`` and
``nonce_b64`` are stripped from the header. The manifest carries the chain inputs instead,
so the chain, digest, proof and signature are all checkable without decrypting anything,
and the bundle discloses only the one event it is about.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any

from evidence_api.locator import Located, VaultLocator
from evidence_api.verifier import VerifyReport
from evidence_api.verify_template import VERIFY_PY

README = """VEYRA evidence export
=====================

event_uid : {event_uid}
segment   : {segment_id}
exported  : {exported_at}

This bundle proves that the bytes in raw.bin were archived by VEYRA and have not changed
since, without needing access to the vault or any secret key:

    raw.bin -> raw_sha256 -> hash chain -> segment digest -> Merkle root -> signature

To check it yourself:

    python verify.py

Every check is printed PASS/FAIL, and the exit code is 0 only if all of them pass. The
script needs Python 3 and the `cryptography` package (for the Ed25519 signature).

Files
-----
raw.bin               the exact original bytes, as received
envelope.json         the IF-ENVELOPE record stored in the vault
segment_manifest.json the segment header and the chain inputs of every record in it
proof.json            the Merkle audit path from this segment to the signed root
signed_root.json      the signed window root covering this segment
pubkey.pem            the public key the root was signed with

Note: the segment's encryption key is not part of this bundle, by design. The manifest
carries only what verification needs, so nothing about other events is disclosed.
"""


# Header fields the bundle must not carry: the wrapped data key and its nonce. Nothing in
# verify.py needs them, and an export goes to people outside the deployment.
_WITHHELD_HEADER_FIELDS = ("wrapped_dek_b64", "nonce_b64")


def public_header(header: dict[str, Any]) -> dict[str, Any]:
    """The segment header minus its key material."""
    return {k: v for k, v in header.items() if k not in _WITHHELD_HEADER_FIELDS}


def segment_manifest(located: Located, locator: VaultLocator) -> dict[str, Any]:
    """The segment header plus each record's chain inputs, so the chain is recomputable."""
    reader = locator.reader(located.path)
    records = []
    for record in reader.records():
        envelope = json.loads(record.envelope_bytes)
        records.append(
            {
                "record_idx": record.record_idx,
                "offset": record.offset,
                "event_uid": envelope["event_uid"],
                "raw_sha256": envelope["raw_sha256"],
                "chain_hash_hex": record.chain_hash_hex,
            }
        )
    return {
        "header": public_header(located.header),
        "segment_digest_hex": located.digest_hex,
        "records": records,
    }


def build_export(located: Located, report: VerifyReport, locator: VaultLocator) -> bytes:
    """The zip bytes. ``report`` supplies the Merkle proof already computed by verify."""
    envelope = located.envelope
    raw = base64.b64decode(envelope["raw_b64"], validate=True)
    entry = located.ledger_entry
    signed_root = (
        {"payload": entry.payload, "sig_b64": entry.sig_b64}
        if entry is not None
        else {"payload": None, "sig_b64": None}
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("raw.bin", raw)
        zf.writestr("envelope.json", _json(envelope))
        zf.writestr("segment_manifest.json", _json(segment_manifest(located, locator)))
        zf.writestr("proof.json", _json(report.proof))
        zf.writestr("signed_root.json", _json(signed_root))
        zf.writestr("pubkey.pem", locator.public_key_pem())
        zf.writestr("verify.py", VERIFY_PY)
        zf.writestr(
            "README.txt",
            README.format(
                event_uid=located.event_uid,
                segment_id=located.segment_id,
                exported_at=datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    return buffer.getvalue()


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


EXPORT_FILES = (
    "raw.bin",
    "envelope.json",
    "segment_manifest.json",
    "proof.json",
    "signed_root.json",
    "pubkey.pem",
    "verify.py",
    "README.txt",
)
