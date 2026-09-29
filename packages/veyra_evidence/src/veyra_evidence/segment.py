"""IF-SEGMENT — the vault's on-disk format: sealed, compressed, encrypted segments.

Layout of ``data/vault/<topic>/<partition>/<segment_id>.seg``::

    magic "VEYRASEG1"
    u32 header_len (big-endian)
    header_json
    ciphertext                  # AES-256-GCM( zstd( record blob ) )

The record blob is a sequence of ``u32 len_be || envelope_json_bytes``. The **full**
envelope is stored, ``raw_b64`` included: this file is the evidence, so the raw bytes must
come back out exactly as the edge stamped them (P1).

The GCM AAD is the header JSON **without** ``wrapped_dek_b64`` and ``nonce_b64`` — every
other header field (segment id, offsets, record count, chain hashes, blob hash) is
therefore cryptographically bound to the ciphertext and cannot be edited undetected.

Prototype scope (B2): write, seal, read back, verify. No chattr, no crash injection.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import stat
import struct
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import zstandard
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from veyra_common.settings import Settings, settings
from veyra_evidence.chain import genesis, step
from veyra_evidence.digest import segment_digest_hex
from veyra_evidence.keys import NONCE_BYTES, KeyProvider

log = logging.getLogger(__name__)

MAGIC = b"VEYRASEG1"
SEGMENT_SUFFIX = ".seg"
TMP_SUFFIX = ".tmp"
SEALED_MODE = 0o444  # read-only for everyone once sealed
_U32 = struct.Struct(">I")
# Header fields excluded from the AAD: they carry the key material itself.
_AAD_EXCLUDE = ("wrapped_dek_b64", "nonce_b64")


class SegmentError(Exception):
    """A segment could not be written, read or verified."""


class SegmentIntegrityError(SegmentError):
    """The segment's ciphertext, AAD or hashes do not agree — possible tampering."""


def segment_id(topic: str, partition: int, first_offset: int) -> str:
    """IF-NAMING: ``seg_<topic>_<partition>_<first_offset zero-padded to 12>``."""
    return f"seg_{topic}_{partition}_{first_offset:012d}"


def segment_dir(topic: str, partition: int, vault_dir: Path | None = None) -> Path:
    base = vault_dir if vault_dir is not None else settings.vault_dir
    return Path(base) / topic / str(partition)


def aad_bytes(header: dict[str, Any]) -> bytes:
    """Canonical JSON of the header minus the key fields (sorted keys, compact)."""
    bound = {k: v for k, v in header.items() if k not in _AAD_EXCLUDE}
    return json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class SealedSegment:
    """What the archiver needs after a seal: where it is and what to publish."""

    path: Path
    header: dict[str, Any]
    digest_hex: str

    @property
    def segment_id(self) -> str:
        return str(self.header["segment_id"])

    @property
    def first_offset(self) -> int:
        return int(self.header["first_offset"])

    @property
    def last_offset(self) -> int:
        return int(self.header["last_offset"])

    @property
    def record_count(self) -> int:
        return int(self.header["record_count"])


@dataclass(frozen=True, slots=True)
class Record:
    """One archived envelope, as read back out of a segment."""

    record_idx: int
    offset: int
    envelope_bytes: bytes
    chain_hash_hex: str

    @property
    def envelope(self) -> dict[str, Any]:
        return json.loads(self.envelope_bytes)


class SegmentWriter:
    """Accumulates envelopes for one topic-partition, then seals them into one file.

    Records are held in memory until :meth:`seal`; ``VEYRA_SEGMENT_MAX_BYTES`` (2 MB on
    the laptop profile) bounds that. :meth:`should_seal` tells the archiver when to stop.
    """

    def __init__(
        self,
        topic: str,
        partition: int,
        first_offset: int,
        prev_chain_hash: bytes | None = None,
        key_provider: KeyProvider | None = None,
        *,
        chain_epoch: int = 0,
        vault_dir: Path | None = None,
        cfg: Settings | None = None,
    ) -> None:
        self.cfg = cfg or settings
        self.topic = topic
        self.partition = partition
        self.first_offset = first_offset
        self.chain_epoch = chain_epoch
        self.vault_dir = Path(vault_dir) if vault_dir is not None else self.cfg.vault_dir
        if key_provider is None:
            from veyra_evidence.keys import get_key_provider

            key_provider = get_key_provider(self.cfg)
        self.keys = key_provider

        self.prev_chain_hash = (
            genesis(topic, partition, chain_epoch) if prev_chain_hash is None else prev_chain_hash
        )
        self.chain_head = self.prev_chain_hash
        self.segment_id = segment_id(topic, partition, first_offset)
        self.created_at = _now()
        self._records: list[bytes] = []
        self._chain_hashes: list[bytes] = []
        self._offsets: list[int] = []
        self.blob_bytes = 0
        self.sealed = False

    # ---------------------------------------------------------------- append
    def append(
        self, offset: int, envelope_bytes: bytes, raw_sha256: str, event_uid: str
    ) -> tuple[int, str]:
        """Add one envelope; returns ``(record_idx, chain_hash_hex)`` for IF-VAULT-INDEX."""
        if self.sealed:
            raise SegmentError(f"{self.segment_id} is already sealed")
        if self._offsets and offset <= self._offsets[-1]:
            # The chain is only meaningful in offset order (IF-CHAIN).
            raise SegmentError(
                f"offsets must increase: {offset} after {self._offsets[-1]} in {self.segment_id}"
            )
        self.chain_head = step(self.chain_head, raw_sha256, event_uid, offset)
        self._records.append(envelope_bytes)
        self._chain_hashes.append(self.chain_head)
        self._offsets.append(offset)
        self.blob_bytes += _U32.size + len(envelope_bytes)
        return len(self._records) - 1, self.chain_head.hex()

    # ---------------------------------------------------------------- policy
    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def age_seconds(self) -> float:
        started = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        return (datetime.now(UTC) - started).total_seconds()

    def should_seal(self, *, max_records: int | None = None) -> bool:
        """Size or age reached the profile's limit (``max_records`` is an extra bound)."""
        if not self._records:
            return False
        if self.blob_bytes >= self.cfg.segment_max_bytes:
            return True
        if max_records is not None and self.record_count >= max_records:
            return True
        return self.age_seconds >= self.cfg.segment_max_seconds

    # ---------------------------------------------------------------- seal
    def seal(self) -> SealedSegment:
        """Compress, encrypt and write the segment; the file ends up read-only."""
        if self.sealed:
            raise SegmentError(f"{self.segment_id} is already sealed")
        if not self._records:
            raise SegmentError(f"{self.segment_id} has no records to seal")

        blob = b"".join(_U32.pack(len(r)) + r for r in self._records)
        compressed = zstandard.ZstdCompressor(level=self.cfg.zstd_level).compress(blob)
        dek, key_id, wrapped = self.keys.new_data_key()
        nonce = os.urandom(NONCE_BYTES)

        header: dict[str, Any] = {
            "segment_id": self.segment_id,
            "topic": self.topic,
            "partition": self.partition,
            "chain_epoch": self.chain_epoch,
            "first_offset": self._offsets[0],
            "last_offset": self._offsets[-1],
            "record_count": len(self._records),
            # Additive to IF-SEGMENT: the Kafka offset of each record, in order. The
            # chain hashes offsets, IF-ENVELOPE does not carry one, and a partition may
            # have gaps — so a verifier working from files alone needs them here.
            "offsets": list(self._offsets),
            "prev_chain_hash_hex": self.prev_chain_hash.hex(),
            "last_chain_hash_hex": self.chain_head.hex(),
            "blob_sha256_hex": hashlib.sha256(blob).hexdigest(),
            "key_id": key_id,
            "wrapped_dek_b64": base64.b64encode(wrapped).decode(),
            "nonce_b64": base64.b64encode(nonce).decode(),
            "zstd_level": self.cfg.zstd_level,
            "created_at": self.created_at,
            "sealed_at": _now(),
        }
        ciphertext = AESGCM(dek).encrypt(nonce, compressed, aad_bytes(header))

        directory = segment_dir(self.topic, self.partition, self.vault_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.segment_id}{SEGMENT_SUFFIX}"
        tmp = directory / f"{self.segment_id}{TMP_SUFFIX}"
        header_json = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        # Write to .tmp and fsync, then rename: a reader never sees a partial segment.
        with open(tmp, "wb") as fh:
            fh.write(MAGIC)
            fh.write(_U32.pack(len(header_json)))
            fh.write(header_json)
            fh.write(ciphertext)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
        self._fsync_dir(directory)
        os.chmod(path, SEALED_MODE)

        self.sealed = True
        sealed = SealedSegment(path, header, segment_digest_hex(header))
        log.info(
            "segment sealed",
            extra={
                "segment_id": self.segment_id,
                "records": len(self._records),
                "bytes": path.stat().st_size,
                "path": str(path),
            },
        )
        return sealed

    @staticmethod
    def _fsync_dir(directory: Path) -> None:
        """Make the rename itself durable, not just the file contents."""
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ---------------------------------------------------------------- index
    def index_rows(self) -> list[tuple[int, int, str]]:
        """``(record_idx, offset, chain_hash_hex)`` per record, for IF-VAULT-INDEX."""
        return [
            (idx, offset, h.hex())
            for idx, (offset, h) in enumerate(zip(self._offsets, self._chain_hashes, strict=True))
        ]


class SegmentReader:
    """Reads a sealed segment, verifying GCM and the blob hash.

    Any tampering — a flipped ciphertext bit, an edited header field, a swapped nonce —
    surfaces as :class:`SegmentIntegrityError`.
    """

    def __init__(
        self,
        path: Path | str,
        key_provider: KeyProvider | None = None,
        *,
        cfg: Settings | None = None,
    ) -> None:
        self.cfg = cfg or settings
        self.path = Path(path)
        if key_provider is None:
            from veyra_evidence.keys import get_key_provider

            key_provider = get_key_provider(self.cfg)
        self.keys = key_provider
        self._header: dict[str, Any] | None = None
        self._blob: bytes | None = None

    # ---------------------------------------------------------------- header
    def header(self) -> dict[str, Any]:
        """The segment header. Cheap: no decryption."""
        if self._header is None:
            with open(self.path, "rb") as fh:
                if fh.read(len(MAGIC)) != MAGIC:
                    raise SegmentError(f"{self.path} is not a VEYRA segment")
                (header_len,) = _U32.unpack(fh.read(_U32.size))
                raw = fh.read(header_len)
                if len(raw) != header_len:
                    raise SegmentError(f"{self.path} header is truncated")
                try:
                    self._header = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise SegmentError(f"{self.path} header is not JSON: {exc}") from exc
                self._payload_offset = len(MAGIC) + _U32.size + header_len
        return self._header

    def digest_hex(self) -> str:
        return segment_digest_hex(self.header())

    # ---------------------------------------------------------------- payload
    def _plaintext(self) -> bytes:
        if self._blob is not None:
            return self._blob
        header = self.header()
        with open(self.path, "rb") as fh:
            fh.seek(self._payload_offset)
            ciphertext = fh.read()
        try:
            dek = self.keys.unwrap(header["key_id"], base64.b64decode(header["wrapped_dek_b64"]))
            nonce = base64.b64decode(header["nonce_b64"])
            compressed = AESGCM(dek).decrypt(nonce, ciphertext, aad_bytes(header))
        except InvalidTag as exc:
            raise SegmentIntegrityError(
                f"{self.path}: GCM verification failed — ciphertext or header was modified"
            ) from exc
        except Exception as exc:  # unwrap failure, bad base64
            raise SegmentIntegrityError(f"{self.path}: cannot decrypt — {exc}") from exc
        try:
            blob = zstandard.ZstdDecompressor().decompress(compressed)
        except zstandard.ZstdError as exc:
            raise SegmentIntegrityError(f"{self.path}: decompression failed — {exc}") from exc
        actual = hashlib.sha256(blob).hexdigest()
        if actual != header["blob_sha256_hex"]:
            raise SegmentIntegrityError(
                f"{self.path}: blob_sha256 mismatch (header {header['blob_sha256_hex']}, "
                f"actual {actual})"
            )
        self._blob = blob
        return blob

    def records(self) -> Iterator[Record]:
        """Every record in offset order, with its chain hash recomputed as we go."""
        header = self.header()
        blob = self._plaintext()
        offsets = _offsets_of(header)
        head = bytes.fromhex(header["prev_chain_hash_hex"])
        view = memoryview(blob)
        pos = 0
        for idx in range(int(header["record_count"])):
            if pos + _U32.size > len(view):
                raise SegmentIntegrityError(f"{self.path}: record {idx} length is truncated")
            (length,) = _U32.unpack(view[pos : pos + _U32.size])
            pos += _U32.size
            if pos + length > len(view):
                raise SegmentIntegrityError(f"{self.path}: record {idx} body is truncated")
            envelope_bytes = bytes(view[pos : pos + length])
            pos += length
            envelope = json.loads(envelope_bytes)
            head = step(head, envelope["raw_sha256"], envelope["event_uid"], offsets[idx])
            yield Record(idx, offsets[idx], envelope_bytes, head.hex())

    def record(self, idx: int) -> Record:
        for record in self.records():
            if record.record_idx == idx:
                return record
        raise SegmentError(f"{self.path} has no record {idx}")

    def verify_chain(self) -> str:
        """Recompute the chain from the records; returns the head, or raises on a break."""
        header = self.header()
        head = str(header["prev_chain_hash_hex"])
        for record in self.records():
            head = record.chain_hash_hex
        expected = header["last_chain_hash_hex"]
        if head != expected:
            raise SegmentIntegrityError(
                f"{self.path}: chain head mismatch (header {expected}, recomputed {head})"
            )
        return head

    def is_read_only(self) -> bool:
        return not bool(self.path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _offsets_of(header: dict[str, Any]) -> list[int]:
    """Per-record offsets from the header, falling back to contiguous ones."""
    offsets = header.get("offsets")
    count = int(header["record_count"])
    if offsets is None:
        return [int(header["first_offset"]) + i for i in range(count)]
    if len(offsets) != count:
        raise SegmentIntegrityError(f"header lists {len(offsets)} offsets for {count} records")
    return [int(o) for o in offsets]


def find_segments(topic: str, partition: int, vault_dir: Path | None = None) -> list[Path]:
    """Sealed segments for one partition, in offset order (the id sorts naturally)."""
    directory = segment_dir(topic, partition, vault_dir)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"*{SEGMENT_SUFFIX}"))


def latest_header(
    topic: str,
    partition: int,
    vault_dir: Path | None = None,
    key_provider: KeyProvider | None = None,
) -> dict[str, Any] | None:
    """The newest sealed segment's header, for resuming the chain at startup."""
    paths = find_segments(topic, partition, vault_dir)
    if not paths:
        return None
    return SegmentReader(paths[-1], key_provider).header()
