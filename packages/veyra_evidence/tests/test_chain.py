"""IF-CHAIN against the frozen vectors (02_CONTRACTS §IF-CHAIN, spec_vectors.py).

These vectors are frozen after S0: if this file fails, the chain implementation changed
and every existing vault is invalidated. That is a breaking change, never a fix.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from veyra_evidence.chain import genesis, step, walk

TOPIC, PARTITION = "raw.acme", 0

# From docs/plan/reference/spec_vectors.py, out["chain"] — the authority named by the
# contract, and cross-checked against it by test_matches_the_reference_implementation.
# (The table printed in 02_CONTRACTS.md §IF-CHAIN was stale; corrected alongside this.)
H0 = "3d78bdc3924694f97ccedf7d55e171922e7e08bbffc4203fc40aa1845f250820"

RAWS = [
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=neel.k FAILED login '
    b'from 45.12.3.9 via 10.2.3.4 attempts:3"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
    b"<86>Sep 26 14:05:12 core-lnx-07 sshd[4410]: Failed password for invalid user admin "
    b"from 45.12.3.9 port 52144 ssh2",
    b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny",
]
UIDS = [
    "0192a4f0-0000-7000-8000-000000000001",
    "0192a4f0-0000-7000-8000-000000000002",
    "0192a4f0-0000-7000-8000-000000000003",
]
EXPECTED = [
    "f1e002eb0c8402663629e37addc2a7406d7a03ed83ad83ad0fdc4a5cb9338429",
    "93527c00a526f77d0373a09977fa605421aea7aa92ec824c16f05af64cc3c085",
    "b1586fbf2b233f4cf5249401e522580cc4a7d7b99a81b2116455d8970e96aeb7",
]
STEPS = [
    (hashlib.sha256(raw).hexdigest(), uid, 100 + i)
    for i, (raw, uid) in enumerate(zip(RAWS, UIDS, strict=True))
]


def test_genesis_matches_the_vector() -> None:
    assert genesis(TOPIC, PARTITION).hex() == H0


def test_every_step_matches_the_vectors() -> None:
    head = genesis(TOPIC, PARTITION)
    for (raw_sha, uid, offset), expected in zip(STEPS, EXPECTED, strict=True):
        head = step(head, raw_sha, uid, offset)
        assert head.hex() == expected


def test_walk_folds_the_whole_chain() -> None:
    assert walk(TOPIC, PARTITION, STEPS).hex() == EXPECTED[-1]


def test_walk_can_continue_an_existing_chain() -> None:
    """How a segment continues the previous segment's head."""
    first = walk(TOPIC, PARTITION, STEPS[:1])
    assert walk(TOPIC, PARTITION, STEPS[1:], start=first).hex() == EXPECTED[-1]


def test_genesis_is_per_topic_and_partition() -> None:
    assert genesis(TOPIC, 0) != genesis(TOPIC, 1)
    assert genesis("raw.acme", 0) != genesis("raw.linux", 0)


def test_epoch_zero_is_the_frozen_genesis_and_other_epochs_differ() -> None:
    """Epoch 0 must omit the suffix, or the frozen vectors would break."""
    assert genesis(TOPIC, PARTITION, 0).hex() == H0
    assert genesis(TOPIC, PARTITION, 1).hex() != H0


def test_step_accepts_bytes_and_uuid_objects() -> None:
    raw_sha, uid, offset = STEPS[0]
    head = genesis(TOPIC, PARTITION)
    assert step(head, bytes.fromhex(raw_sha), uuid.UUID(uid), offset) == step(
        head, raw_sha, uid, offset
    )


def test_order_matters() -> None:
    """Reordering records must change the head — that is the point of the chain."""
    assert walk(TOPIC, PARTITION, STEPS) != walk(TOPIC, PARTITION, list(reversed(STEPS)))


def test_offset_is_part_of_the_hash() -> None:
    raw_sha, uid, offset = STEPS[0]
    head = genesis(TOPIC, PARTITION)
    assert step(head, raw_sha, uid, offset) != step(head, raw_sha, uid, offset + 1)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"prev": b"short"}, "32 bytes"),
        ({"raw_sha256": "beef"}, "raw_sha256"),
        ({"offset": -1}, "negative"),
    ],
)
def test_step_rejects_malformed_input(kwargs: dict, match: str) -> None:
    args = {
        "prev": genesis(TOPIC, PARTITION),
        "raw_sha256": STEPS[0][0],
        "event_uid": UIDS[0],
        "offset": 1,
    }
    args.update(kwargs)
    with pytest.raises(ValueError, match=match):
        step(**args)  # type: ignore[arg-type]


def test_genesis_rejects_a_negative_partition() -> None:
    with pytest.raises(ValueError, match="negative"):
        genesis(TOPIC, -1)


def test_matches_the_reference_implementation() -> None:
    """Run spec_vectors.py itself, so this file can never drift from the authority."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "docs" / "plan" / "reference" / "spec_vectors.py"
    out = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=True
    )
    chain = json.loads(out.stdout)["chain"]
    assert chain["topic"] == TOPIC and chain["partition"] == PARTITION
    assert chain["steps"][0]["h0"] == genesis(TOPIC, PARTITION).hex()
    head = genesis(TOPIC, PARTITION)
    for want in chain["steps"][1:]:
        head = step(head, want["raw_sha256"], want["event_uid"], want["offset"])
        assert head.hex() == want["h"]
