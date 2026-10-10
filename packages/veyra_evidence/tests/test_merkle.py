"""IF-MERKLE against the frozen vector, plus inclusion proofs (RFC 6962 hashing)."""

from __future__ import annotations

import hashlib

import pytest

from veyra_evidence.merkle import (
    ProofStep,
    empty_root,
    inclusion_proof,
    leaf_hash,
    node_hash,
    root,
    root_hex,
    verify_inclusion,
)

# 02_CONTRACTS §IF-MERKLE / spec_vectors.py: leaf data sha256("segment-0..2").
LEAVES3 = [hashlib.sha256(f"segment-{i}".encode()).digest() for i in range(3)]
ROOT3 = "49a27ff0ee8487600104ea234555fdaa1b3f8ac98d67d12fa3c3620a0e7000f4"


def data(n: int) -> list[bytes]:
    return [hashlib.sha256(f"segment-{i}".encode()).digest() for i in range(n)]


def test_root_matches_the_frozen_vector() -> None:
    assert root_hex(LEAVES3) == ROOT3


def test_matches_the_reference_implementation() -> None:
    """Run spec_vectors.py itself, so this can never drift from the authority."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent / "spec_vectors.py"
    out = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, check=True
    )
    merkle = json.loads(out.stdout)["merkle"]
    assert [d.hex() for d in LEAVES3] == merkle["leaf_data_hex"]
    assert root_hex(LEAVES3) == merkle["root"]


def test_an_empty_window_still_has_a_root() -> None:
    """IF-SIGNED-ROOT: empty windows keep the chain gapless."""
    assert root([]) == empty_root() == hashlib.sha256(b"").digest()


def test_a_single_leaf_root_is_its_leaf_hash() -> None:
    assert root(LEAVES3[:1]) == leaf_hash(LEAVES3[0])


def test_two_leaves_combine_the_leaf_hashes() -> None:
    assert root(LEAVES3[:2]) == node_hash(leaf_hash(LEAVES3[0]), leaf_hash(LEAVES3[1]))


def test_three_leaves_split_at_two() -> None:
    """k = the largest power of 2 below n, so 3 splits as (2, 1) — not (1, 2)."""
    expected = node_hash(
        node_hash(leaf_hash(LEAVES3[0]), leaf_hash(LEAVES3[1])), leaf_hash(LEAVES3[2])
    )
    assert root(LEAVES3) == expected


def test_leaf_and_node_prefixes_differ() -> None:
    """Domain separation: a leaf can never be confused with an internal node."""
    assert leaf_hash(b"x") != node_hash(b"", b"x")
    assert leaf_hash(b"") == hashlib.sha256(b"\x00").digest()
    assert node_hash(b"a", b"b") == hashlib.sha256(b"\x01ab").digest()


def test_order_matters() -> None:
    assert root(LEAVES3) != root(list(reversed(LEAVES3)))


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 31, 32])
def test_every_leaf_has_a_working_inclusion_proof(n: int) -> None:
    leaves = data(n)
    expected = root(leaves)
    for index in range(n):
        proof = inclusion_proof(leaves, index)
        assert verify_inclusion(leaves[index], proof, expected), f"n={n} index={index}"
        # A balanced tree needs ceil(log2(n)) steps; never more.
        assert len(proof) <= max(1, n - 1).bit_length()


def test_proof_accepts_a_hex_root() -> None:
    proof = inclusion_proof(LEAVES3, 1)
    assert verify_inclusion(LEAVES3[1], proof, ROOT3)


def test_a_proof_for_the_wrong_leaf_fails() -> None:
    proof = inclusion_proof(LEAVES3, 0)
    assert not verify_inclusion(LEAVES3[1], proof, root(LEAVES3))


def test_a_tampered_proof_step_fails() -> None:
    proof = inclusion_proof(LEAVES3, 0)
    broken = [ProofStep(bytes(32), step.sibling_is_left) for step in proof]
    assert not verify_inclusion(LEAVES3[0], broken, root(LEAVES3))


def test_flipping_a_sibling_side_fails() -> None:
    """The left/right position is part of the proof, not a detail."""
    leaves = data(4)
    proof = inclusion_proof(leaves, 1)
    flipped = [ProofStep(step.hash, not step.sibling_is_left) for step in proof]
    assert not verify_inclusion(leaves[1], flipped, root(leaves))


def test_a_proof_does_not_verify_against_another_root() -> None:
    leaves = data(4)
    proof = inclusion_proof(leaves, 2)
    assert verify_inclusion(leaves[2], proof, root(leaves))
    assert not verify_inclusion(leaves[2], proof, root(data(5)))


def test_changing_any_leaf_changes_the_root() -> None:
    """This is what makes a signed root cover every segment in its window."""
    leaves = data(5)
    before = root(leaves)
    for index in range(len(leaves)):
        mutated = list(leaves)
        mutated[index] = hashlib.sha256(b"forged").digest()
        assert root(mutated) != before


def test_proof_steps_round_trip_through_json() -> None:
    """The evidence API serves proofs as JSON (B4)."""
    proof = inclusion_proof(LEAVES3, 2)
    restored = [ProofStep.from_json(step.as_json()) for step in proof]
    assert restored == proof
    assert verify_inclusion(LEAVES3[2], restored, root(LEAVES3))


def test_an_out_of_range_index_is_refused() -> None:
    for index in (-1, 3):
        with pytest.raises(IndexError):
            inclusion_proof(LEAVES3, index)
    with pytest.raises(IndexError):
        inclusion_proof([], 0)
