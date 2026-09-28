# Track A — Data plane (Person A)

```
contracts: v1.4
```

## Mission

Make every log, however messy, flow from the network into Wazuh, stamped, parsed deterministically at the best achievable tier, traceable byte-for-byte, and never dropped. Track A owns the hot path.

## You own

**Directories:**
- `edge/`
- `packages/veyra_engine/`
- `services/{ingest_gateway,normalizer,router}/`
- `wazuh/`

**Interfaces:** IF-ENVELOPE, IF-INVENTORY (the Vector side), IF-ENGINE-LIB, IF-OCSF-SUBSET, IF-ULPF, IF-NORM-EVENT, IF-TEMPLATE-SIG, IF-ROUTES, IF-WAZUH, IF-LINEAGE, IF-DLQ, IF-SHADOW, IF-RECEIPT.

## Phases

| Phase | Title | Depends on | Feeds checkpoint |
|---|---|---|---|
| A1 | Edge collectors (Vector): stamping, framing, resolution | S0 | CP1 |
| A2 | Ingest gateway: HEC-compatible push, API keys, quotas, batch | S0, C1 (key publish; mock until then) | CP3 |
| A3 | Engine core + normalizer service (tiers 1, 2, 4; peeling; transactions) | S0 | CP1 |
| A4 | Tier 3 generic extraction, field offsets, classifier, robustness | A3 | CP2 |
| A5 | Shadow, backtest library, replay consumption, revisions | A4, C2 (candidate publish; mock until then) | CP3 |
| A6 | Router + Wazuh rules + throughput bench | A3 (minimal for CP1), A5 (revisions) | CP1, CP3 |

**Suggested parallel pairs (two agents):**

| Pair | Why it works |
|---|---|
| A1 with A3 | Config vs Python |
| A2 with A4 | Different services |
| A6 with A5 | Router vs engine |

Do a **minimal A6** (a route to the Wazuh file sink plus rule 100100) right after A3 so CP1 can pass. Finish A6 later.

## Consumers of your work

| Track | Uses |
|---|---|
| B | Envelopes on `raw.*` (archiver, indexer); `lineage`, `norm.*`, `dlq`, `shadow`, `receipts` (indexer); `veyra_engine.provenance_check` and `field_offsets` (B6 UI highlights) |
| C | `veyra_engine` in-process for golden tests, backtests, onboarding preview, token extraction for LLM drafting. **Keep IF-ENGINE-LIB stable**; C depends on it heavily. |

## Invariants you guard (from `00_MASTER.md` §4)

- **P1:** raw bytes are never modified after stamping.
- **P2:** every event goes to `norm.*` at some tier.
- **P3:** no LLM calls, network calls or clock reads inside `veyra_engine.normalize`.
- **P4:** every mapped value has an offset or is listed in `derived_fields`.
