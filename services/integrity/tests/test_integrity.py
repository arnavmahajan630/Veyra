"""B3 prototype: window roots are signed, chained, and tamper-evident.

These run on a vault built by the real B2 :class:`SegmentWriter`, so the digests being
signed are the ones the archiver actually produces. No Kafka and no containers needed.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest
from integrity.integrity import Integrity, parse_sealed_at
from integrity.settings import IntegritySettings

from veyra_common.envelope import stamp
from veyra_common.models import SignedRoot
from veyra_evidence.keys import LocalKeyProvider, verify_signature
from veyra_evidence.ledger import (
    GENESIS_PREV,
    LedgerEntry,
    canonical_bytes,
    payload_sha256,
    read_ledger,
)
from veyra_evidence.merkle import inclusion_proof, root_hex, verify_inclusion
from veyra_evidence.segment import SegmentWriter

WINDOW = 60
TOPIC = "raw.custom"


@pytest.fixture
def cfg(tmp_path: Path) -> IntegritySettings:
    return IntegritySettings(
        _env_file=None,
        data_dir=tmp_path,
        merkle_window_seconds=WINDOW,
        integrity_window_lag_seconds=0,
        metrics_port=0,
    )


@pytest.fixture
def keys(cfg: IntegritySettings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


def write_segment(
    cfg: IntegritySettings, keys: LocalKeyProvider, partition: int, first_offset: int
) -> str:
    """Seal one real segment and return its digest hex."""
    writer = SegmentWriter(
        TOPIC, partition, first_offset, key_provider=keys, vault_dir=cfg.vault_dir, cfg=cfg
    )
    env = stamp(
        f"<134>Sep 26 14:05:11 fw01 app[233]: event {partition}/{first_offset}".encode(),
        collector_id="it",
        transport="syslog_tcp",
        framing_method="newline",
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
    )
    writer.append(first_offset, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
    return writer.seal().digest_hex


def build_vault(cfg: IntegritySettings, keys: LocalKeyProvider, count: int = 3) -> list[str]:
    return [write_segment(cfg, keys, i, i * 100) for i in range(count)]


# ---------------------------------------------------------------- signing
def test_a_window_root_is_signed_and_covers_every_segment(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    digests = build_vault(cfg, keys, 3)
    service = Integrity(cfg, keys)
    # Everything sealed just now, so pretend the window has closed.
    entries = service.run_once(now=service.window_start_of(_now(service)) + 2 * WINDOW)
    assert len(entries) == 1

    entry = entries[0]
    root = entry.as_root()
    assert isinstance(root, SignedRoot)
    assert root.leaf_count == 3
    assert sorted(root.segments) == sorted(s.segment_id for s in service.scan_segments())
    assert root.prev_signed_sha256 == GENESIS_PREV
    assert root.key_id == keys.signing_key_id
    assert root.alg == "Ed25519"
    # The root is the Merkle tree over the segment digests, in (sealed_at, id) order.
    ordered = [bytes.fromhex(d) for d in _ordered_digests(service)]
    assert root.root == root_hex(ordered)
    assert set(_ordered_digests(service)) == set(digests)


def test_the_signature_verifies_with_only_the_public_key(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    build_vault(cfg, keys, 2)
    service = Integrity(cfg, keys)
    entry = service.run_once(now=_far_future(service))[0]
    public_pem = keys.public_key_pem(keys.signing_key_id)
    assert "PUBLIC KEY" in public_pem
    assert verify_signature(public_pem, entry.canonical, entry.signature)


def test_a_modified_payload_breaks_its_signature(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    build_vault(cfg, keys, 2)
    service = Integrity(cfg, keys)
    entry = service.run_once(now=_far_future(service))[0]
    forged = dict(entry.payload) | {"leaf_count": 99}
    public_pem = keys.public_key_pem(keys.signing_key_id)
    assert not verify_signature(public_pem, canonical_bytes(forged), entry.signature)


def test_an_inclusion_proof_places_one_segment_under_the_signed_root(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """What B4's verify endpoint needs: one segment, a proof, a signed root."""
    build_vault(cfg, keys, 4)
    service = Integrity(cfg, keys)
    entry = service.run_once(now=_far_future(service))[0]
    leaves = [bytes.fromhex(d) for d in _ordered_digests(service)]
    for index, leaf in enumerate(leaves):
        proof = inclusion_proof(leaves, index)
        assert verify_inclusion(leaf, proof, entry.as_root().root)


# ---------------------------------------------------------------- chaining
def test_each_root_chains_onto_the_previous_one(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    service = Integrity(cfg, keys)
    # Three segments, forced into three different windows by their sealed_at.
    build_vault(cfg, keys, 3)
    windows = service.group_windows(service.scan_segments())
    # They all land in one window in real time, so drive the signing directly instead.
    members = sorted((s for group in windows.values() for s in group), key=lambda s: s.segment_id)
    entries = [
        service.sign_window(1790000000 + i * WINDOW, [member]) for i, member in enumerate(members)
    ]

    assert entries[0].prev_signed_sha256 == GENESIS_PREV
    for earlier, later in itertools.pairwise(entries):
        assert later.prev_signed_sha256 == payload_sha256(earlier.payload)
    # And the same chain is what the file holds.
    from_disk = read_ledger(service.ledger)
    assert [e.payload for e in from_disk] == [e.payload for e in entries]


def test_editing_an_old_entry_breaks_the_chain(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """The point of prev_signed_sha256: an old root cannot be rewritten quietly."""
    service = Integrity(cfg, keys)
    build_vault(cfg, keys, 2)
    members = sorted(service.scan_segments(), key=lambda s: s.segment_id)
    first = service.sign_window(1790000000, [members[0]])
    second = service.sign_window(1790000000 + WINDOW, [members[1]])
    assert second.prev_signed_sha256 == payload_sha256(first.payload)

    # Rewrite the first entry's root in place.
    lines = service.ledger.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["root"] = "0" * 64
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    service.ledger.write_text("\n".join(lines) + "\n")

    on_disk = read_ledger(service.ledger)
    # The edited entry no longer hashes to what the next entry recorded...
    assert payload_sha256(on_disk[0].payload) != on_disk[1].prev_signed_sha256
    # ...and its own signature no longer covers it.
    public_pem = keys.public_key_pem(keys.signing_key_id)
    assert not verify_signature(public_pem, on_disk[0].canonical, on_disk[0].signature)


# ---------------------------------------------------------------- windows
def test_an_open_window_is_not_signed_yet(cfg: IntegritySettings, keys: LocalKeyProvider) -> None:
    build_vault(cfg, keys, 1)
    service = Integrity(cfg, keys)
    assert service.run_once(now=_now(service)) == []
    assert read_ledger(service.ledger) == []


def test_a_window_is_never_signed_twice(cfg: IntegritySettings, keys: LocalKeyProvider) -> None:
    """A restart must not append a second root for a window already in the ledger."""
    build_vault(cfg, keys, 2)
    service = Integrity(cfg, keys)
    assert len(service.run_once(now=_far_future(service))) == 1
    assert service.run_once(now=_far_future(service)) == []

    restarted = Integrity(cfg, keys)
    assert restarted.run_once(now=_far_future(restarted)) == []
    assert len(read_ledger(service.ledger)) == 1


def test_segments_are_grouped_by_sealed_at_into_windows(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    build_vault(cfg, keys, 2)
    service = Integrity(cfg, keys)
    segments = service.scan_segments()
    assert len(segments) == 2
    for info in segments:
        assert service.window_start_of(info.sealed_at) % WINDOW == 0
        assert info.sealed_at >= service.window_start_of(info.sealed_at)
    windows = service.group_windows(segments)
    assert sum(len(v) for v in windows.values()) == 2


def test_members_are_ordered_by_sealed_at_then_id(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    """IF-MERKLE fixes the leaf order, so the root is deterministic."""
    build_vault(cfg, keys, 4)
    service = Integrity(cfg, keys)
    for members in service.group_windows(service.scan_segments()).values():
        keyed = [(m.sealed_at, m.segment_id) for m in members]
        assert keyed == sorted(keyed)


def test_sealed_at_parses_with_nanoseconds() -> None:
    assert parse_sealed_at("2026-09-26T08:35:30.123456Z") == pytest.approx(1790411730.123456)
    assert parse_sealed_at("2026-09-26T08:35:30Z") == 1790411730.0


def test_an_unreadable_segment_is_skipped_not_fatal(
    cfg: IntegritySettings, keys: LocalKeyProvider
) -> None:
    build_vault(cfg, keys, 1)
    junk = cfg.vault_dir / TOPIC / "9"
    junk.mkdir(parents=True)
    (junk / "seg_broken_9_000000000000.seg").write_bytes(b"NOTVEYRA garbage")
    service = Integrity(cfg, keys)
    assert len(service.scan_segments()) == 1


def test_the_ledger_line_is_canonical_json(cfg: IntegritySettings, keys: LocalKeyProvider) -> None:
    """The signed bytes must be reproducible from the file, byte for byte."""
    build_vault(cfg, keys, 1)
    service = Integrity(cfg, keys)
    entry = service.run_once(now=_far_future(service))[0]
    line = service.ledger.read_text().strip()
    reparsed = LedgerEntry(**json.loads(line))
    assert reparsed.canonical == entry.canonical
    assert reparsed.sha256 == entry.sha256


# ---------------------------------------------------------------- helpers
def _now(service: Integrity) -> float:
    return max(s.sealed_at for s in service.scan_segments())


def _far_future(service: Integrity) -> float:
    """A time by which every sealed segment's window has certainly closed."""
    return _now(service) + 2 * WINDOW


def _ordered_digests(service: Integrity) -> list[str]:
    windows = service.group_windows(service.scan_segments())
    return [m.digest_hex for start in sorted(windows) for m in windows[start]]
