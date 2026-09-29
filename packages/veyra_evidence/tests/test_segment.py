"""IF-SEGMENT: write/read round trip, tamper detection, chain, raw-byte preservation.

The vault is the evidence, so these tests are about exactness and detectability:
what goes in comes out byte-identical, and anything edited afterwards is caught.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

import pytest

from veyra_common.envelope import stamp
from veyra_common.models import Envelope
from veyra_common.settings import Settings
from veyra_evidence.chain import genesis, walk
from veyra_evidence.digest import segment_digest_hex
from veyra_evidence.keys import LocalKeyProvider
from veyra_evidence.segment import (
    MAGIC,
    SealedSegment,
    SegmentError,
    SegmentIntegrityError,
    SegmentReader,
    SegmentWriter,
    aad_bytes,
    find_segments,
    latest_header,
    segment_id,
)

TOPIC, PARTITION = "raw.custom", 1
FIRST_OFFSET = 4400

# A T3 multiline event, Hindi text, and a CEF line: the bytes that must survive intact.
RAWS = [
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)',
    "<134>Sep 26 14:05:12 fw01 app[233]: उपयोगकर्ता लॉगिन विफल".encode(),
    b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny",
    bytes(range(256)),  # every byte value, base64'd inside the envelope
]


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, zstd_level=3, segment_max_bytes=1024)


@pytest.fixture
def keys(cfg: Settings) -> LocalKeyProvider:
    return LocalKeyProvider(cfg=cfg)


def envelopes() -> list[Envelope]:
    return [
        stamp(
            raw,
            collector_id="edge-dmz-01",
            transport="syslog_tcp",
            framing_method="multiline_join" if b"\n" in raw else "newline",
            parts=raw.count(b"\n") + 1,
            source_id="src_authsrv_01",
            tenant_id="t_maha_power",
            vendor="custom",
            zone="dmz",
            listener="dmz-tcp",
            peer_ip="172.20.0.21",
        )
        for raw in RAWS
    ]


def write_segment(
    cfg: Settings, keys: LocalKeyProvider, *, count: int | None = None
) -> tuple[SegmentWriter, list[Envelope], SealedSegment]:
    """Append ``count`` envelopes and seal. Returns the writer, envelopes and sealed info."""
    envs = envelopes()[: count or len(RAWS)]
    writer = SegmentWriter(TOPIC, PARTITION, FIRST_OFFSET, key_provider=keys, cfg=cfg)
    for i, env in enumerate(envs):
        writer.append(
            FIRST_OFFSET + i, env.model_dump_json().encode(), env.raw_sha256, env.event_uid
        )
    return writer, envs, writer.seal()


# ---------------------------------------------------------------- round trip
def test_round_trip_returns_every_record(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, envs, sealed = write_segment(cfg, keys)
    reader = SegmentReader(sealed.path, keys, cfg=cfg)
    records = list(reader.records())
    assert len(records) == len(envs)
    assert [r.record_idx for r in records] == list(range(len(envs)))
    assert [r.offset for r in records] == [FIRST_OFFSET + i for i in range(len(envs))]


def test_raw_bytes_come_back_exactly(cfg: Settings, keys: LocalKeyProvider) -> None:
    """P1: the archived envelope is the evidence — byte-identical, hash still valid."""
    _, envs, sealed = write_segment(cfg, keys)
    reader = SegmentReader(sealed.path, keys, cfg=cfg)
    for original, record in zip(envs, reader.records(), strict=True):
        assert record.envelope_bytes == original.model_dump_json().encode()
        restored = Envelope.model_validate_json(record.envelope_bytes)
        assert restored.raw_bytes == original.raw_bytes
        assert restored.raw_sha256 == original.raw_sha256
        assert restored.hash_matches(), "the stored envelope must still verify"
    # ...including the raw payload that is not valid UTF-8 at all.
    last = Envelope.model_validate_json(list(reader.records())[-1].envelope_bytes)
    assert last.raw_bytes == bytes(range(256))


def test_header_describes_the_segment(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, envs, sealed = write_segment(cfg, keys)
    header = SegmentReader(sealed.path, keys, cfg=cfg).header()
    assert header["segment_id"] == segment_id(TOPIC, PARTITION, FIRST_OFFSET)
    assert header["topic"] == TOPIC
    assert header["partition"] == PARTITION
    assert header["record_count"] == len(envs)
    assert header["first_offset"] == FIRST_OFFSET
    assert header["last_offset"] == FIRST_OFFSET + len(envs) - 1
    assert header["chain_epoch"] == 0
    assert header["zstd_level"] == cfg.zstd_level
    assert header["offsets"] == [FIRST_OFFSET + i for i in range(len(envs))]
    assert header["prev_chain_hash_hex"] == genesis(TOPIC, PARTITION).hex()
    # IF-NAMING: seg_<topic>_<partition>_<12-digit offset>
    assert sealed.path.name == f"seg_{TOPIC}_{PARTITION}_000000004400.seg"


def test_the_file_starts_with_the_magic(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    assert sealed.path.read_bytes()[: len(MAGIC)] == MAGIC


def test_the_payload_is_encrypted_on_disk(cfg: Settings, keys: LocalKeyProvider) -> None:
    """A plaintext search of the file must not find the log line."""
    _, envs, sealed = write_segment(cfg, keys)
    blob = sealed.path.read_bytes()
    assert b"a.sharma" not in blob
    assert b"103.21.4.77" not in blob
    assert envs[0].raw_b64.encode() not in blob


def test_sealed_files_are_read_only(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    mode = sealed.path.stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    assert SegmentReader(sealed.path, keys, cfg=cfg).is_read_only()
    with pytest.raises(PermissionError), open(sealed.path, "ab") as fh:
        fh.write(b"x")


def test_no_temp_file_is_left_behind(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    assert list(sealed.path.parent.glob("*.tmp")) == []


# ---------------------------------------------------------------- chain
def test_chain_recomputes_from_the_file_alone(cfg: Settings, keys: LocalKeyProvider) -> None:
    """B2 AC2: a verifier with only the vault files can rebuild the chain."""
    _, envs, sealed = write_segment(cfg, keys)
    reader = SegmentReader(sealed.path, keys, cfg=cfg)
    assert reader.verify_chain() == sealed.header["last_chain_hash_hex"]
    expected = walk(
        TOPIC,
        PARTITION,
        [(e.raw_sha256, e.event_uid, FIRST_OFFSET + i) for i, e in enumerate(envs)],
    )
    assert sealed.header["last_chain_hash_hex"] == expected.hex()


def test_a_second_segment_continues_the_chain(cfg: Settings, keys: LocalKeyProvider) -> None:
    first_writer, envs, first = write_segment(cfg, keys, count=2)
    second = SegmentWriter(
        TOPIC,
        PARTITION,
        FIRST_OFFSET + 2,
        prev_chain_hash=bytes.fromhex(first.header["last_chain_hash_hex"]),
        key_provider=keys,
        cfg=cfg,
    )
    rest = envelopes()[2:]
    for i, env in enumerate(rest):
        second.append(
            FIRST_OFFSET + 2 + i, env.model_dump_json().encode(), env.raw_sha256, env.event_uid
        )
    sealed2 = second.seal()
    assert sealed2.header["prev_chain_hash_hex"] == first.header["last_chain_hash_hex"]
    # The head across both segments equals one continuous walk over all four records.
    every = [(e.raw_sha256, e.event_uid, FIRST_OFFSET + i) for i, e in enumerate(envs + rest)]
    assert sealed2.header["last_chain_hash_hex"] == walk(TOPIC, PARTITION, every).hex()
    assert first_writer.chain_head.hex() == first.header["last_chain_hash_hex"]


def test_index_rows_match_what_was_appended(cfg: Settings, keys: LocalKeyProvider) -> None:
    """These rows become IF-VAULT-INDEX records."""
    writer, envs, sealed = write_segment(cfg, keys)
    rows = writer.index_rows()
    assert len(rows) == len(envs)
    assert [r[0] for r in rows] == list(range(len(envs)))
    assert [r[1] for r in rows] == [FIRST_OFFSET + i for i in range(len(envs))]
    assert rows[-1][2] == sealed.header["last_chain_hash_hex"]


def test_digest_is_stable_and_binds_the_header(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    assert sealed.digest_hex == segment_digest_hex(sealed.header)
    assert sealed.digest_hex == SegmentReader(sealed.path, keys, cfg=cfg).digest_hex()
    tampered = dict(sealed.header) | {"record_count": sealed.record_count + 1}
    assert segment_digest_hex(tampered) != sealed.digest_hex


# ---------------------------------------------------------------- tampering
def rewrite(path: Path, data: bytes) -> None:
    """Sealed segments are 0444, so an attacker would have to chmod first — as here."""
    os.chmod(path, 0o644)
    path.write_bytes(data)


def test_a_flipped_ciphertext_bit_is_detected(cfg: Settings, keys: LocalKeyProvider) -> None:
    """B2 AC4."""
    _, _, sealed = write_segment(cfg, keys)
    blob = bytearray(sealed.path.read_bytes())
    blob[-1] ^= 0x01
    rewrite(sealed.path, bytes(blob))
    with pytest.raises(SegmentIntegrityError, match="GCM"):
        list(SegmentReader(sealed.path, keys, cfg=cfg).records())


def test_truncating_the_ciphertext_is_detected(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    blob = sealed.path.read_bytes()
    rewrite(sealed.path, blob[:-8])
    with pytest.raises(SegmentIntegrityError):
        list(SegmentReader(sealed.path, keys, cfg=cfg).records())


@pytest.mark.parametrize(
    "field",
    ["segment_id", "first_offset", "last_offset", "record_count", "last_chain_hash_hex", "offsets"],
)
def test_editing_a_header_field_is_detected(
    cfg: Settings, keys: LocalKeyProvider, field: str
) -> None:
    """The AAD binds every header field except the key material (B2 design note)."""
    _, _, sealed = write_segment(cfg, keys)
    header = dict(sealed.header)
    header[field] = (
        "seg_forged_0_000000000000"
        if field == "segment_id"
        else ([0] if field == "offsets" else ("0" * 64 if field.endswith("_hex") else 99999))
    )
    _replace_header(sealed.path, header)
    with pytest.raises(SegmentIntegrityError):
        list(SegmentReader(sealed.path, keys, cfg=cfg).records())


def test_swapping_the_nonce_is_detected(cfg: Settings, keys: LocalKeyProvider) -> None:
    """The nonce is not in the AAD, so GCM itself must catch this."""
    _, _, sealed = write_segment(cfg, keys)
    header = dict(sealed.header)
    header["nonce_b64"] = base64.b64encode(b"\x00" * 12).decode()
    _replace_header(sealed.path, header)
    with pytest.raises(SegmentIntegrityError):
        list(SegmentReader(sealed.path, keys, cfg=cfg).records())


def test_a_foreign_key_cannot_read_the_segment(cfg: Settings, tmp_path: Path) -> None:
    """A stolen segment is useless without the KEK that wrapped its DEK."""
    keys = LocalKeyProvider(cfg=cfg)
    _, _, sealed = write_segment(cfg, keys)
    other = LocalKeyProvider(keys_dir=tmp_path / "other-keys", cfg=cfg)
    with pytest.raises(SegmentIntegrityError):
        list(SegmentReader(sealed.path, other, cfg=cfg).records())


def _replace_header(path: Path, header: dict) -> None:
    """Rewrite a segment file with a different header, keeping the ciphertext."""
    import struct

    blob = path.read_bytes()
    (old_len,) = struct.unpack(">I", blob[len(MAGIC) : len(MAGIC) + 4])
    ciphertext = blob[len(MAGIC) + 4 + old_len :]
    new = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    rewrite(path, MAGIC + struct.pack(">I", len(new)) + new + ciphertext)


def test_a_non_segment_file_is_rejected(
    cfg: Settings, keys: LocalKeyProvider, tmp_path: Path
) -> None:
    path = tmp_path / "not-a-segment.seg"
    path.write_bytes(b"NOTVEYRA" + b"\x00" * 32)
    with pytest.raises(SegmentError, match="not a VEYRA segment"):
        SegmentReader(path, keys, cfg=cfg).header()


def test_aad_excludes_only_the_key_material(cfg: Settings, keys: LocalKeyProvider) -> None:
    _, _, sealed = write_segment(cfg, keys)
    aad = json.loads(aad_bytes(sealed.header))
    assert "wrapped_dek_b64" not in aad and "nonce_b64" not in aad
    assert set(aad) == set(sealed.header) - {"wrapped_dek_b64", "nonce_b64"}


# ---------------------------------------------------------------- writer rules
def test_appending_after_seal_is_refused(cfg: Settings, keys: LocalKeyProvider) -> None:
    writer, envs, _ = write_segment(cfg, keys)
    env = envs[0]
    with pytest.raises(SegmentError, match="already sealed"):
        writer.append(9999, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)


def test_offsets_must_increase(cfg: Settings, keys: LocalKeyProvider) -> None:
    """Out-of-order records would make the chain meaningless."""
    env = envelopes()[0]
    writer = SegmentWriter(TOPIC, PARTITION, 10, key_provider=keys, cfg=cfg)
    writer.append(10, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
    with pytest.raises(SegmentError, match="offsets must increase"):
        writer.append(10, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)


def test_sealing_an_empty_segment_is_refused(cfg: Settings, keys: LocalKeyProvider) -> None:
    writer = SegmentWriter(TOPIC, PARTITION, 0, key_provider=keys, cfg=cfg)
    assert not writer.should_seal()
    with pytest.raises(SegmentError, match="no records"):
        writer.seal()


def test_should_seal_on_size(cfg: Settings, keys: LocalKeyProvider) -> None:
    writer = SegmentWriter(TOPIC, PARTITION, 0, key_provider=keys, cfg=cfg)
    env = envelopes()[0]
    payload = env.model_dump_json().encode()
    assert not writer.should_seal()
    offset = 0
    while writer.blob_bytes < cfg.segment_max_bytes:
        writer.append(offset, payload, env.raw_sha256, env.event_uid)
        offset += 1
    assert writer.should_seal()


def test_should_seal_on_record_count(cfg: Settings, keys: LocalKeyProvider) -> None:
    writer = SegmentWriter(TOPIC, PARTITION, 0, key_provider=keys, cfg=cfg)
    env = envelopes()[0]
    writer.append(0, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
    assert writer.should_seal(max_records=1)
    assert not writer.should_seal(max_records=2)


# ---------------------------------------------------------------- discovery
def test_find_segments_and_latest_header(cfg: Settings, keys: LocalKeyProvider) -> None:
    """How the archiver resumes a chain after a restart."""
    _, _, first = write_segment(cfg, keys, count=2)
    second = SegmentWriter(
        TOPIC,
        PARTITION,
        FIRST_OFFSET + 2,
        prev_chain_hash=bytes.fromhex(first.header["last_chain_hash_hex"]),
        key_provider=keys,
        cfg=cfg,
    )
    env = envelopes()[2]
    second.append(FIRST_OFFSET + 2, env.model_dump_json().encode(), env.raw_sha256, env.event_uid)
    sealed2 = second.seal()

    found = find_segments(TOPIC, PARTITION, cfg.vault_dir)
    assert [p.name for p in found] == [first.path.name, sealed2.path.name]
    header = latest_header(TOPIC, PARTITION, cfg.vault_dir, keys)
    assert header is not None
    assert header["segment_id"] == sealed2.segment_id
    assert latest_header("raw.nothing", 0, cfg.vault_dir, keys) is None
