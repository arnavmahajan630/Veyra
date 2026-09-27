# B3 — Integrity: windows, Merkle roots, signing, chained roots, immudb ledger

```
track: B   owner: B   status: todo
contracts: v1.2
depends_on: [B2]     unblocks: [CP2, B4]
consumes: [IF-VAULT-INDEX (segment summaries), IF-KEYPROVIDER]
provides: [IF-MERKLE, IF-SIGNED-ROOT, window_roots table rows]
directories: [packages/veyra_evidence/, services/integrity/]
```

## Goal

Every window (60 s on the laptop), compute an RFC 6962 Merkle root over the digests of segments sealed in that window. Sign it with Ed25519, **chain it to the previous signed root**, and store it in immudb plus a local ledger.

Chaining gives consistency across windows, an improvement over v1's independent hourly roots: rewriting any past window breaks every later link.

## Design

### `veyra_evidence.merkle`
- `root(leaves_data) -> bytes`
- `inclusion_proof(leaves_data, index) -> list[(side, hash)]`
- `verify_inclusion(leaf_data, proof, root) -> bool`

Per IF-MERKLE. Test vectors, plus property tests for n = 1..200.

### Integrity service
- Consumes `vault_index`, **segment summaries only**, group `integrity`.
- Buffers by window, using `sealed_at` floored to `MERKLE_WINDOW_SECONDS`.
- A window closes at `window_end + VEYRA_WINDOW_GRACE_SECONDS` (default 5), based on the wall clock plus a watermark of max `sealed_at` seen.
- **Late segments** (arriving after close) go to the *current* open window, with a `late:true` flag recorded in the payload's `segments` entries as `seg_id!late`. Document this; it's rare.
- **On close:**
  1. Order the leaves by `(sealed_at, segment_id)`.
  2. Compute the root.
  3. Build the canonical payload (IF-SIGNED-ROOT) with `prev_signed_sha256`.
  4. Sign via KeyProvider.
  5. Write to immudb (verified set).
  6. Append to `ledger.ndjson` (fsync).
  7. Insert into ClickHouse `window_roots` (and update `segments.window_id`).
- **Empty windows** produce roots too (no gaps in the chain).
- **State:** the last signed payload hash is persisted in `data/state/integrity_head.json` and re-derived from the ledger at startup (ledger = truth; immudb = independent verifiable copy). Offsets are committed after the window's writes succeed.

### immudb spike (first task)
Decide between two client paths and record the choice in IF-SIGNED-ROOT (clarification):
- the Python SDK with `verifiedSet` / `verifiedGet`;
- pg-wire SQL with verification via the SDK or `immuclient`.

Criterion: B4 must be able to show "immudb verified ✓" per root within 100 ms. The database name comes from `data/state/immudb_db` (B7's reset rotates it).

## Tasks
- [ ] 1. The immudb spike (short, time-boxed) → decision recorded.
- [ ] 2. `merkle.py` + vectors + property tests.
- [ ] 3. Integrity service: windowing, grace, late handling, sign, store ×3.
- [ ] 4. `tools/ledger_audit.py`: walks the ledger, verifies every signature and `prev` link, and cross-checks immudb.
- [ ] 5. Public key publish: `data/keys/root_ed25519.pub.pem`, also exposed later by B4.
- [ ] 6. Stretch: the `OpenBaoKeyProvider`, and the `docker-compose.secure.yml` wiring (transit keys: `veyra-dek` (aes256), `veyra-root` (ed25519)).

## Acceptance criteria
- [ ] AC1: With traffic, one root per window appears within `window + grace + 2 s`; the ledger audit passes.
- [ ] AC2: Edit any byte of an old ledger line → the ledger audit fails at that window, and every later link breaks.
- [ ] AC3: immudb verified-get succeeds for every root; latency < 100 ms.
- [ ] AC4: Restart integrity mid-window → no duplicate or missing roots; the chain stays continuous.

## Settings
`VEYRA_MERKLE_WINDOW_SECONDS`, `VEYRA_WINDOW_GRACE_SECONDS`, `VEYRA_IMMUDB_*`.

## Implementation notes
_(filled after execution)_
