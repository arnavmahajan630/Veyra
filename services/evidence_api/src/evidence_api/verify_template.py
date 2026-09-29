"""The ``verify.py`` shipped inside every evidence export.

Kept as a string so the exported script is a single self-contained file. It re-derives the
whole chain of evidence from the bundle, using only ``hashlib`` and ``cryptography``:

    raw.bin -> raw_sha256 -> hash chain -> segment digest -> Merkle root -> signature

It deliberately does **not** need the vault, the API, or any VEYRA package: an auditor
unzips the bundle and runs it.
"""

from __future__ import annotations

VERIFY_PY = '''#!/usr/bin/env python3
"""Verify a VEYRA evidence export. Usage: python verify.py [bundle_dir]

Checks, in order:
  1. raw.bin hashes to the raw_sha256 in envelope.json
  2. the envelope in the segment manifest is the one in envelope.json
  3. the per-partition hash chain recomputes to the segment's last_chain_hash (IF-CHAIN)
  4. the segment digest recomputes from the manifest header (IF-SEGMENT)
  5. that digest is included under signed_root.json's root (IF-MERKLE)
  6. the root payload carries a valid Ed25519 signature (IF-SIGNED-ROOT)

Needs nothing but Python 3: it uses `cryptography` for the signature when that package is
installed, and falls back to a built-in Ed25519 implementation when it is not.
Exit code 0 = PASS, 1 = FAIL.
"""

import base64
import hashlib
import json
import sys
import uuid
from pathlib import Path

GENESIS_LABEL = b"VEYRA-GENESIS"
SEG_LABEL = b"VEYRA-SEG"
SEP = b"\\x1f"


def sha256(data):
    return hashlib.sha256(data).digest()


# ---------------------------------------------------------------- IF-CHAIN
def chain_genesis(topic, partition, epoch=0):
    payload = GENESIS_LABEL + SEP + topic.encode() + SEP + str(partition).encode()
    if epoch:
        payload += SEP + str(epoch).encode()
    return sha256(payload)


def chain_step(prev, raw_sha256_hex, event_uid, offset):
    return sha256(
        prev
        + bytes.fromhex(raw_sha256_hex)
        + uuid.UUID(event_uid).bytes
        + int(offset).to_bytes(8, "big")
    )


# ---------------------------------------------------------------- IF-MERKLE
def leaf_hash(data):
    return sha256(b"\\x00" + data)


def node_hash(left, right):
    return sha256(b"\\x01" + left + right)


def verify_inclusion(leaf_data, proof, expected_root_hex):
    running = leaf_hash(leaf_data)
    for step in proof:
        sibling = bytes.fromhex(step["hash"])
        running = (
            node_hash(sibling, running) if step["sibling_is_left"] else node_hash(running, sibling)
        )
    return running == bytes.fromhex(expected_root_hex)


# ---------------------------------------------------------------- IF-SEGMENT
def segment_digest(header):
    return sha256(
        SEG_LABEL
        + SEP
        + str(header["segment_id"]).encode()
        + SEP
        + bytes.fromhex(header["prev_chain_hash_hex"])
        + bytes.fromhex(header["last_chain_hash_hex"])
        + int(header["record_count"]).to_bytes(8, "big")
        + bytes.fromhex(header["blob_sha256_hex"])
    )


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()



# ------------------------------------------------- Ed25519 (RFC 8032), no dependencies
# Used when `cryptography` is not installed, so this bundle verifies on a stock Python 3.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _Q - 2, _Q)) % _Q
_SQRT_M1 = pow(2, (_Q - 1) // 4, _Q)
_PEM_PREFIX = bytes.fromhex("302a300506032b6570032100")  # SPKI header for Ed25519


def _recover_x(y, sign):
    if y >= _Q:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q) % _Q
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_Q + 3) // 8, _Q)
    if (x * x - x2) % _Q != 0:
        x = x * _SQRT_M1 % _Q
    if (x * x - x2) % _Q != 0:
        return None
    if (x & 1) != sign:
        x = _Q - x
    return x


_GY = 4 * pow(5, _Q - 2, _Q) % _Q
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _Q)


def _point_add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % _Q
    b = (y1 + x1) * (y2 + x2) % _Q
    c = t1 * 2 * _D * t2 % _Q
    dd = z1 * 2 * z2 % _Q
    e, f, g, h = b - a, dd - c, dd + c, b + a
    return (e * f % _Q, g * h % _Q, f * g % _Q, e * h % _Q)


def _point_mul(scalar, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(p1, p2):
    x1, y1, z1, _ = p1
    x2, y2, z2, _ = p2
    return (x1 * z2 - x2 * z1) % _Q == 0 and (y1 * z2 - y2 * z1) % _Q == 0


def _decode_point(data):
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _Q)


def _pubkey_bytes_from_pem(pem):
    """The raw 32-byte key out of a PEM SubjectPublicKeyInfo."""
    body = "".join(line for line in pem.splitlines() if "-----" not in line)
    der = base64.b64decode(body)
    if not der.startswith(_PEM_PREFIX) or len(der) != len(_PEM_PREFIX) + 32:
        raise ValueError("not an Ed25519 SubjectPublicKeyInfo")
    return der[len(_PEM_PREFIX) :]


def ed25519_verify(pubkey_pem, message, signature):
    """Pure-Python Ed25519 verification (RFC 8032 section 5.1.7)."""
    if len(signature) != 64:
        return False
    a = _decode_point(_pubkey_bytes_from_pem(pubkey_pem))
    r = _decode_point(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    k = int.from_bytes(
        hashlib.sha512(signature[:32] + _pubkey_bytes_from_pem(pubkey_pem) + message).digest(),
        "little",
    ) % _L
    # [S]B must equal R + [k]A
    return _point_equal(_point_mul(s, _G), _point_add(r, _point_mul(k, a)))


def main():
    base = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    raw = (base / "raw.bin").read_bytes()
    envelope = json.loads((base / "envelope.json").read_text())
    manifest = json.loads((base / "segment_manifest.json").read_text())
    proof = json.loads((base / "proof.json").read_text())
    signed_root = json.loads((base / "signed_root.json").read_text())
    pubkey_pem = (base / "pubkey.pem").read_text()

    header = manifest["header"]
    records = manifest["records"]
    results = []

    # 1. the raw bytes are what the envelope claims
    actual = hashlib.sha256(raw).hexdigest()
    results.append(("hash_raw", actual == envelope["raw_sha256"], actual))

    # 2. the manifest's record for this event matches the exported envelope
    mine = [r for r in records if r["event_uid"] == envelope["event_uid"]]
    results.append(
        (
            "record_present",
            len(mine) == 1 and mine[0]["raw_sha256"] == envelope["raw_sha256"],
            f"{len(mine)} matching record(s)",
        )
    )

    # 3. the hash chain over the segment's records
    head = bytes.fromhex(header["prev_chain_hash_hex"])
    for record in records:
        head = chain_step(head, record["raw_sha256"], record["event_uid"], record["offset"])
    chain_ok = head.hex() == header["last_chain_hash_hex"]
    results.append(("chain_walk", chain_ok, head.hex()))
    # The first segment of a partition must start at the genesis hash.
    if int(header["first_offset"]) == 0:
        expected = chain_genesis(
            header["topic"], header["partition"], header.get("chain_epoch", 0)
        ).hex()
        results.append(
            ("chain_genesis", header["prev_chain_hash_hex"] == expected, expected)
        )

    # 4. the segment digest
    digest = segment_digest(header)
    claimed = manifest["segment_digest_hex"]
    results.append(("segment_digest", digest.hex() == claimed, digest.hex()))

    # 5. inclusion under the signed root
    results.append(
        (
            "merkle_inclusion",
            verify_inclusion(digest, proof, signed_root["payload"]["root"]),
            signed_root["payload"]["root"],
        )
    )

    # 6. the signature over the canonical root payload
    signature = base64.b64decode(signed_root["sig_b64"])
    payload_bytes = canonical(signed_root["payload"])
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        key = load_pem_public_key(pubkey_pem.encode())
        try:
            key.verify(signature, payload_bytes)
            sig_ok, sig_detail = True, signed_root["payload"]["key_id"] + " (cryptography)"
        except InvalidSignature:
            sig_ok, sig_detail = False, "Ed25519 verification failed"
    except ImportError:
        # No third-party packages available: use the built-in implementation.
        sig_ok = ed25519_verify(pubkey_pem, payload_bytes, signature)
        sig_detail = (
            signed_root["payload"]["key_id"] + " (built-in)"
            if sig_ok
            else "Ed25519 verification failed"
        )
    results.append(("root_signature", sig_ok, sig_detail))

    width = max(len(name) for name, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)}  {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print()
    print(f"{'PASS' if not failed else 'FAIL'}: event {envelope['event_uid']}")
    if failed:
        print("failed checks: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
'''
