# B4 — Evidence API: lineage endpoints, verify, evidence export

```
track: B   owner: B   status: todo
contracts: v1.2
depends_on: [B1, B3]     unblocks: [CP2, B5, B6, C5 (overview/sources data), C2 (raw fetch for replay/backtest)]
consumes: [IF-CH-SCHEMA, IF-SEGMENT, IF-MERKLE, IF-SIGNED-ROOT, IF-KEYPROVIDER]
provides: [IF-API-EVIDENCE]
directories: [services/evidence_api/, packages/veyra_evidence/verify.py]
```

## Goal

One FastAPI service that answers "where is this event, what happened to it, and can you prove it?":
- the lineage endpoints the console needs;
- an 8-step cryptographic verification that runs in under 2 s;
- a self-contained evidence export that an auditor can verify offline with nothing but Python.

v1 refs: §8.3, §10.3, §16 (Console → data plane contracts). Demo beat 5.

## Design

### Raw retrieval: `fetch_raw(event_uid)`
- If `vault_locations.sealed`: open the segment (LRU cache of decrypted segments, `VEYRA_EVIDENCE_SEG_CACHE`) and read `record_idx`.
- Otherwise: fetch from Kafka by `raw_ref` (assign, seek, poll one) — "controlled evidence retrieval". Responses mark `source: vault|kafka`.
- Access is audited (IF-AUDIT) with the actor from a forwarded header. Caddy passes the session user; control-api validates and sets `X-Veyra-User`. For the demo this is trusted on the internal network; document it.

### `verify(event_uid)` (`veyra_evidence.verify`, pure given its inputs)

| id | Label (UI) | Check |
|---|---|---|
| `fetch_raw` | Raw bytes retrieved from vault | Segment exists, record found |
| `decrypt_segment` | Segment decrypts and authenticates | AES-GCM tag valid with AAD |
| `hash_raw` | Matches ingest-time hash | `sha256(raw)` == envelope `raw_sha256` **and** == ClickHouse `raw_events.raw_sha256` (the independent copy recorded at ingest) |
| `chain_walk` | Hash chain intact | Recompute from `header.prev_chain_hash` through all records == `header.last_chain_hash`; this record's chain value == index |
| `segment_digest` | Segment digest recomputed | IF-SEGMENT digest from recomputed values |
| `merkle_inclusion` | Included in signed window root | The segment is listed in its window's payload; the inclusion proof verifies against `root` |
| `root_signature` | Root signed by VEYRA key | Ed25519 verify of the canonical payload |
| `immudb_verified` | Root anchored in immudb | Verified-get matches the payload |

The order in the UI follows the table. **Every step runs even if an earlier one fails** (where possible), so the UI can show exactly where the break is. Each step returns `{id, label, ok, detail, ms}`.

Performance: cache segment decrypts and window payloads. Target < 2 s cold and < 300 ms warm.

### Export (`POST /evidence/export/{uid}`) produces a zip containing:

| File | Contents |
|---|---|
| `raw.bin` | The exact raw bytes |
| `envelope.json` | The stamped envelope |
| `segment_manifest.json` | For every record in the segment: `event_uid`, `offset`, `raw_sha256` (enough to recompute the chain), plus the segment header without the wrapped key |
| `proof.json` | Merkle audit path, leaf index, window id |
| `signed_root.json` | Payload + signature |
| `pubkey.pem` | Public key |
| `verify.py` | **Standalone**: stdlib + `cryptography` only. Recomputes raw hash → manifest → chain → digest → Merkle → signature, and prints PASS/FAIL per step. |
| `SECTION63_TECHNICAL.md` | Pre-filled technical particulars for the BSA 2023 §63 workflow: system description, hash values, times, custody history. Includes a clear note that certification and admissibility are legal determinations by the responsible official. |

### Lineage endpoints
Per IF-API-EVIDENCE, implemented only via `veyra_lineage.queries`. SSE `/lineage/stream` sends an overview tick every `SSE_TICK_MS`.

## Tasks
- [ ] 1. Service skeleton, Caddy routes, auth header handling, audit emission.
- [ ] 2. Lineage endpoints over the query library; OpenAPI clean (C5 generates TS types from it).
- [ ] 3. `fetch_raw` (vault and Kafka paths).
- [ ] 4. `verify` with all 8 steps + per-step tests (happy path, plus each step failing in isolation using crafted fixtures).
- [ ] 5. Export zip + standalone `verify.py`, tested in a clean venv.
- [ ] 6. `/evidence/roots` and `/evidence/pubkey`.
- [ ] 7. Latency benchmark for verify (cold and warm).

## Acceptance criteria
- [ ] AC1: Verify on a sealed event → 8/8 ok, in < 2 s cold and < 300 ms warm.
- [ ] AC2: An unsealed (fresh) event → verify returns `fetch_raw` ok (kafka), and the steps from `segment_digest` onward read "pending seal" (not failure). The UI shows "sealing in ≤ N s".
- [ ] AC3: The exported zip verifies with `python verify.py` in a clean venv → all PASS.
- [ ] AC4: `event_detail` returns revisions 1 and 2 for a replayed event, with both OCSF bodies and receipts.

## Implementation notes
_(filled after execution)_
