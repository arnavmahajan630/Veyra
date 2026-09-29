# B2 — Archiver: segments, hash chain, encryption, immutability

```
track: B   owner: B   status: todo
contracts: v1.5
depends_on: [S0]     unblocks: [CP1, B3]
consumes: [IF-ENVELOPE, IF-TOPICS]
provides: [IF-SEGMENT, IF-CHAIN (implementation), IF-VAULT-INDEX, IF-KEYPROVIDER (local)]
directories: [packages/veyra_evidence/, services/archiver/]
```

## Goal

Store the exact raw envelope of every event (v1 §8.1) in sealed, encrypted, immutable segments per topic-partition. Hash-chain them (v1 §8.2), and publish the event → segment index atomically with the offset commit, so a crash can never lose or double-seal evidence.

## Design

### `veyra_evidence`
- **`chain.py`:** `genesis(topic, partition)` and `step(prev, raw_sha256_hex, event_uid, offset)`, per IF-CHAIN. Tests against the vectors.
- **`segment.py`:**
  - `SegmentWriter(topic, partition, first_offset, prev_chain_hash, key_provider)` with `.append(offset, envelope_bytes, raw_sha256, event_uid) -> (record_idx, chain_hash)` and `.seal() -> SealedSegment` (header, path, digest).
  - `SegmentReader(path, key_provider)` with `.header()`, `.records()` and `.record(idx)`. It verifies GCM on read.
- **Format per IF-SEGMENT.** AES-256-GCM with a 12-byte random nonce. **AAD = the header JSON bytes, excluding `wrapped_dek_b64` and `nonce_b64`**, so header fields can't be swapped without detection.
- **`digest.py`:** `segment_digest(header)` per IF-SEGMENT.
- **`keys.py`:**
  - `LocalKeyProvider`: KEK = 32 random bytes in `data/keys/kek.bin`; the DEK is wrapped with AES-KW or AES-GCM under the KEK; the Ed25519 private key is in `data/keys/root_ed25519.pem`. Files are created on first boot with mode 0400.
  - `OpenBaoKeyProvider` (transit: `datakey`, `decrypt`, `sign`) behind `VEYRA_KEY_PROVIDER=openbao`. Implement it in B3 or as stretch, but define the class now.
  - A bootstrap CLI, `python -m veyra_evidence.keys init`, that also creates `data/keys/route_hmac` for A6 (log it in the changelog).

### Archiver service
- Consumer: pattern `^raw\..*`, group `archiver`, manual assignment awareness. On partition revoke, **seal open segments first**, then release.
- **Per-partition state:** an open `SegmentWriter` or none; the chain head.
- **Startup:** for each assigned partition, find the latest sealed segment header in `data/vault/<topic>/<partition>/` and resume the chain from its `last_chain_hash`. The committed offset must equal `last_offset + 1`. If not (anomaly), log an alert and rebuild from the committed offset with a *new* chain epoch recorded in the header (`chain_epoch`). Document it; it should never happen.
- **Seal** when bytes ≥ `SEGMENT_MAX_BYTES`, age ≥ `SEGMENT_MAX_SECONDS`, or on shutdown or revoke.
- **Seal procedure:**
  1. Write to `*.tmp`, then fsync.
  2. Rename to `.seg` and fsync the directory.
  3. chmod 0444.
  4. `chattr +i` if `VAULT_CHATTR=1` (via `ioctl FS_IOC_SETFLAGS`, or by calling `chattr`, with `cap_add: LINUX_IMMUTABLE`).
  5. **In a Kafka transaction:** produce the IF-VAULT-INDEX event records plus the segment summary, `send_offsets_to_transaction(last_offset+1)`, then commit.
- **Crash between rename and commit:** on restart, the segment exists but offsets are not committed. Detect it (header `last_offset` ≥ committed offset), re-emit its index records in a transaction, and commit. This makes sealing idempotent. Test it explicitly.
- **Metrics:**
  - `veyra_vault_segments_sealed_total`
  - `veyra_vault_bytes_total`
  - `veyra_vault_open_segment_age_seconds{partition}`
  - `veyra_vault_seal_seconds`

## Tasks
- [ ] 1. `chain`, `segment`, `digest`, `keys` (local) + unit tests (vectors, round-trip, GCM tamper detection, AAD binding).
- [ ] 2. Archiver service: per-partition writers, seal policy, transactional index publish.
- [ ] 3. Startup resume + crash-idempotence (inject crashes at each seal step with an env flag).
- [ ] 4. Immutability: 0444 + chattr; a test that a normal write fails.
- [ ] 5. Integration test with `fake_raw` at 200 EPS for 2 min: every `event_uid` appears in exactly one segment; the chain recomputes from segment files alone.

## Acceptance criteria
- [ ] AC1: Segments seal within `SEGMENT_MAX_SECONDS`; files are read-only; chattr is applied where enabled.
- [ ] AC2: `tools/vault_audit.py` recomputes every chain from the files, and every event on `raw.*` is in exactly one segment.
- [ ] AC3: Crash injection at each step → after restart, no missing or duplicate records, and chain continuity holds.
- [ ] AC4: Decrypting with a flipped ciphertext bit raises an integrity error.

## Settings
`VEYRA_SEGMENT_MAX_BYTES`, `VEYRA_SEGMENT_MAX_SECONDS`, `VEYRA_ZSTD_LEVEL`, `VEYRA_VAULT_DIR`, `VEYRA_VAULT_CHATTR`, `VEYRA_KEY_PROVIDER`, `VEYRA_KEYS_DIR`.

## Implementation notes
_(filled after execution)_
