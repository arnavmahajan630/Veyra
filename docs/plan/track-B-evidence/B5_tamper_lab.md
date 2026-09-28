# B5 — Tamper lab

```
track: B   owner: B   status: todo
contracts: v1.4
depends_on: [B4]     unblocks: [CP3, Beat 5, "tamper matrix" slide]
consumes: [IF-SEGMENT, IF-SIGNED-ROOT, IF-API-DEMO (tamper endpoints)]
provides: [tools/tamper.py, demo-engine tamper hooks, tamper matrix]
directories: [tools/tamper/, services/demo_engine/tamper.py, docs (tamper matrix)]
```

## Goal

Prove, with repeatable attacks of increasing strength, that verification detects and **locates** tampering. Provide instant, reversible tamper actions for the live demo.

## Attack modes

Each attack backs up the target file(s) to `data/tamper_backup/` first, so `untamper` restores them exactly.

| Mode | Attacker capability | What it does | Expected failing steps |
|---|---|---|---|
| `naive_flip` | Root on the vault disk | `chattr -i`, flip one ciphertext byte of the target's segment | `decrypt_segment` (and downstream steps that need plaintext report "unavailable") |
| `insider_rewrite` | Root **plus the data keys** | Decrypt, change the target record's raw bytes (swap the IP string for a same-length one), recompute its `raw_sha256` in the envelope, recompute the whole segment chain and header (`last_chain_hash`, `blob_sha256`), re-encrypt with a fresh nonce, rewrite the file | `hash_raw` (ingest-time copy mismatch), `segment_digest` / `merkle_inclusion` (leaf no longer in the signed root). `root_signature` ok, `immudb_verified` ok. |
| `segment_delete` | Root | Delete the segment file | `fetch_raw` (missing); window payload lists a missing segment |
| `root_rewrite` | Root on the ledger | Rewrite the root in the ledger line to match a forged tree | `root_signature` fails; `immudb_verified` mismatch |
| `index_rewrite` (stretch) | ClickHouse write access | Change `raw_events.raw_sha256` to the forged value too | Still caught by `merkle_inclusion` |

Verify must produce a human-readable `detail` for the failing steps, for example: *"Segment seg_raw.custom_1_…: digest 7a3f… is not a leaf of signed root w_1790000060 (signature valid). The segment was altered after sealing."*

## Tasks
- [ ] 1. `tools/tamper.py <mode> --event <uid>` + `untamper`.
- [ ] 2. The demo-engine endpoints `/tamper` and `/untamper` (IF-API-DEMO) call the same functions.
- [ ] 3. The tamper-matrix test: for each mode, assert the exact set of failing step ids, then untamper and assert all green.
- [ ] 4. `docs/tamper_matrix.md`, used as a slide and a Q&A reference.

## Acceptance criteria
- [ ] AC1: The tamper-matrix test passes for all modes, 5 runs each.
- [ ] AC2: `insider_rewrite` + verify → red at exactly the expected steps, with the locating `detail` message; untamper → green.
- [ ] AC3: The tamper endpoint responds in < 1 s (the demo hotkey).

## Implementation notes
_(filled after execution)_
