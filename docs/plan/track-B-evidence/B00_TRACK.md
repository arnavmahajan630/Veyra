# Track B — Evidence, lineage, demo engine (Person B)

```
contracts: v1.1
```

## Mission

Make every event provable and findable:
- sealed, hash-chained and signed evidence (v1 §8);
- a fast lineage index (v1 §10.3);
- APIs and pages that show traceability and verification;
- the tamper lab.

Track B also owns the **demo engine**, which makes the 3-minute run reproducible.

## You own

**Directories:**
- `packages/veyra_evidence/`, `packages/veyra_lineage/`
- `services/{archiver,integrity,lineage_indexer,evidence_api,demo_engine}/`
- `demo/`
- `console/src/pages/{lineage,evidence,demo}/`

**Interfaces:** IF-CHAIN, IF-SEGMENT, IF-MERKLE, IF-SIGNED-ROOT, IF-KEYPROVIDER, IF-VAULT-INDEX, IF-CH-SCHEMA, IF-API-EVIDENCE, IF-API-DEMO.

## Phases

| Phase | Title | Depends on | Feeds |
|---|---|---|---|
| B1 | Lineage indexer + ClickHouse schema + query library | S0 | CP1 |
| B2 | Archiver: segments, chain, encryption, immutability | S0 | CP1 |
| B3 | Integrity: windows, Merkle, signing, immudb, ledger | B2 | CP2 |
| B4 | Evidence API: lineage endpoints, verify, export | B1, B3 | CP2 |
| B5 | Tamper lab | B4 | CP3 |
| B6 | Console pages: Lineage + Evidence | B4, C5 (shell) | CP3 |
| B7 | Demo engine: scenario, reset, stages, preflight, demo panel | B5, C1 | CP4 |

**Parallel pairs:**

| Pair | Why it works |
|---|---|
| B1 with B2 | Indexer vs archiver |
| B3 with B6 (components first) | Backend vs UI |
| B5 with B7 | Different services |

B is the most back-loaded track, because B6 and B7 depend on others. **If B finishes B1–B5 early, help C with C6 or pre-build B7's scenario runner.**

## Consumers of your work

| Track | Uses |
|---|---|
| C | Lineage and overview endpoints (C5 Overview/Sources pages); raw fetch for replay (C2); `templates/{sig}/events` (C2 backtests) |
| A | `data/keys/route_hmac` via KeyProvider bootstrap (coordinate) |
| Everyone | The demo engine and `make demo-*` |
