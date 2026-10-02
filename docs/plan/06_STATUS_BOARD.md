# 06 — STATUS BOARD

Update your row after every phase (status, one line, report link). Status values: `todo`, `in-progress`, `blocked(<why>)`, `done`, `cut`.

| Phase | Owner | Status | Contracts | Summary | Report |
|---|---|---|---|---|---|
| S0 Bootstrap | all (run by A) | done | v1.1 | AC1–AC7 pass; stack idles at 2.89 GiB; 77 unit + 5 integration tests green. LLM runs on CPU (53–91 s/draft) — GPU unresolved, @C | [S0.md](reports/S0.md) |
| A1 Edge collectors (Vector) | A | done | v1.2 | 5/5 ACs pass; UUIDv7 + byte-accurate raw_len; T3 joins to one envelope; 60 s Kafka outage lossless; inventory reload verified | [A1.md](reports/A1.md) |
| A2 Ingest gateway (HTTP push) | A | done | v1.4 | 5/5 ACs; HEC raw/event/batch with per-source keys from `control`, revocation live in <2 s, quotas enforced, 503-never-200 durability; envelopes byte-identical to the edge's parity vectors | [A2.md](reports/A2.md) |
| A3 Engine core + normalizer service | A | done | v1.3 | 5/5 ACs; 2732 EPS/core tier 1; byte-exact offsets incl. inside JSON; purity + cross-process determinism proven; transactional normalizer survives kill -9 | [A3.md](reports/A3.md) |
| A4 Tier 3, offsets, robustness | A | done | v1.3 | 5/5 ACs; unregistered sources now tier 3 with observables + byte offsets (goldens moved 4 -> 3); 50k fuzz cases, 0 exceptions; provenance_check enforces both halves; crash-loop journal survives process death | [A4.md](reports/A4.md) |
| A5 Shadow, replay, revisions | A | done | v1.5 | 4/4 ACs; canary compared in shadow on live traffic without touching output; backtest gains field_coverage; replay.raw fixed (every replay message was being DLQ'd as schema_invalid) and revisions are idempotent under kill -9 | [A5.md](reports/A5.md) |
| A6 Router + Wazuh + throughput bench | A | done | v1.5 | Full router with wazuh_main and partner_masked routes, rules 100100-10130, wazuh-logtest verified, bench-throughput | [A6.md](reports/A6.md) |
| B1 Lineage indexer + ClickHouse | B | done | v1.5 | ClickHouse schema (10 tables + 9 MVs), migrations, indexer with dedup, query library | [B1.md](reports/B1.md) |
| B2 Archiver + vault segments | B | done | v1.5 | Segments, hash chain, AES-256-GCM, `0444` (not `chattr +i`), `IF-VAULT-INDEX` published at each seal; keys survive a reset | [B2.md](reports/B2.md) |
| B3 Integrity: Merkle, signing, immudb | B | done | v1.5 | Windows, Merkle tree, Ed25519 signing, prev-hash ledger + audit; immudb anchoring NOT implemented (declared, and reported as such everywhere downstream) | [B3.md](reports/B3.md) |
| B4 Evidence API: verify + export | B | done | v1.5 | 8-step verify, auditor ZIP with an offline verify.py, pubkey, roots; 33 tests. The vault fallback used to fabricate a revision - fixed in B6 | [B4.md](reports/B4.md) |
| B5 Tamper lab | B | done | v1.5 | 4 modes x first-failing-step matrix, backup-before-change, untamper, verify report | [B5.md](reports/B5.md) |
| B6 Console pages: Lineage + Evidence | B | done | v1.5 | 4/5 ACs; real byte offsets plumbed through (the pane had been highlighting hardcoded spans and ticking every field), honest verify reveal, roots live via a new `event: root`, 247 console tests + beat5 Playwright. AC2 is 7 green + 1 grey (immudb prototype) and its <2s cold timing waits for the rehearsal | [B6.md](reports/B6.md) |
| B7 Demo engine + demo panel | B | done (2 ACs deferred) | v1.5 | AC3 + AC4's preflight half pass; scenario at demo/scenarios/sih_main.yaml with seed + the `Failed password` baseline exclusion, multi-line events, 10 measured preflight checks, honest reset, real expectation evaluators, make demo-{reset,preflight,stage,auto}, compose service. AC1 (<90s) and AC2 (demo-auto x10) need the live stack | [B7.md](reports/B7.md) |
| C1 Control API foundation | C | done | v1.4 | Merged (PR #2): auth/roles/tenant scoping, sources + keys, compacted `control` publisher, inventory writer, audit, SSE, seed/reset. Contract compiler (C2 task 1) pulled forward and merged with it. AC3–AC5 pass in unit tests; AC1/AC2 live halves wait for CP1 (normalizer, gateway) | [C1.md](reports/C1.md) |
| C2 Contract registry + compiler + lifecycle | C | in-progress | v1.4 | Merged (PR #3): golden runner, lint, `make contracts-test` (7/7 contracts), lifecycle with four-eyes, backtest, diff, replay jobs. AC1 and AC5 pass; AC3 API half passes; AC2/AC4 pass standalone, live halves need A5 + B (CP3) | [C2.md](reports/C2.md) |
| C3 Drift worker + library packs | C | in-progress | v1.4 | Merged (PR #3): drift worker (Drain3 0.9.11, persisted state), drift inbox with auto-resolve on promote, 5 library packs (76 goldens, all tier 1), `library_match`. AC2–AC4 pass; AC1 grouping passes, live timing smoke still open | [C3.md](reports/C3.md) |
| C4 LLM drafter + provenance + bench | C | in-progress | v1.4 | Merged (PR #5). All 5 ACs pass. Live on the RTX 4050 (2026-09-29): p95 5.3 s (qwen2.5:3b), 6.3 s (llama3.2:3b); `live_then_cache` falls back at 25.05 s. Neither 3B model drafts T3 correctly (verify catches it), so the laptop stays on `cache`, and `llm-cache-seed` was not run. Owner picks `LLM_MODEL` | [C4.md](reports/C4.md) |
| C5 Console shell + design system + Overview/Sources | C | in-progress | v1.4 | Merged (PR #6): `console/` with design tokens + contrast check, EN/HI, hotkeys, SSE with backoff, RawHighlighter + thread (B6 APIs frozen), shell with demo user switch, Overview, Sources + drawer + silent-source alert, mock mode. 126 unit tests, Playwright smoke. AC3–AC5 pass; AC1/AC2 pass on mocks, live halves need B1/B4 (CP2) | [C5.md](reports/C5.md) |
| C6 Console: Onboarding + Contracts/Drift + Delivery/Audit | C | in-progress | v1.5 | Code complete and pushed on branch `c6-console-pages`, PR to `main` pending (not yet merged): /onboard (streamed analysis, compact review, four-eyes, key), /contracts (history, diff, confirmed approve/promote/rollback), /drift (inbox with filters, full review with the thread, submit → approve → promote → replay, 5 s cache fallback, Dismiss), /delivery, /audit (CSV, UTC); control-api `GET /routes`, `POST /onboarding/use-library`, re-draft while drafting. Walkthrough refinements done: a field can't be mapped twice, refused edits/drafts are shown, no endless loading states, mock world matches control-api (audit action names, counts, updated_by). 191 unit tests + Beat 2/4 Playwright flows in mock mode. AC3/AC4 pass; AC1/AC2 pass on mocks, real timing at rehearsal (CP3); AC5 keyboard walkthrough in rehearsal | [C6.md](reports/C6.md) |
| CP1 First light | all | todo | | | |
| CP2 Messy + evidence | all | todo | | | |
| CP3 Loop closed | all | todo | | | |
| CP4 Demo freeze | all | todo | | | |
| S2 Hardening + rehearsal + backup video | all | todo | | | |

## Checkpoint log

| Checkpoint | Date | Result | Notes |
|---|---|---|---|
| A5 | 2026-09-29 | PASS | The contract loop's last step: a canary runs in shadow on live traffic, backtest answers before/after in-process, and replayed events supersede themselves. CP3 now needs A6 from track A, plus B4's evidence-api so C2's replay job can resolve events. |
| A2 | 2026-09-28 | PASS | Push ingest is live: an API key issued on `control` lets a shipper POST to :8088 and land a stamped envelope on `raw.custom`. CP3's onboarding beat now has a real endpoint; still needs C1's key issuance UI (C) and A5. |
| A4 | 2026-09-28 | PASS | Tier 3 live end to end: a messy unregistered line reaches `norm.uncategorized` as tier 3 with both IPs, the user and offsets that slice the raw bytes after Kafka. CP2 now needs A5 + A6 (A), B2-B4 (B), C2-C3 (C). |
| A3 | 2026-09-27 | PASS | Engine + normalizer live: syslog -> raw.linux -> norm.iam tier 1 with real Kafka coordinates. CP1 now needs only minimal A6 from track A, plus B1 + B2 (B) and C1 (C). |
| A1 | 2026-09-27 | PASS | Edge is live on both zones. CP1 still needs A3 + minimal A6 (A), B1 + B2 (B), C1 (C). |
| S0 | 2026-09-27 | PASS | Run solo by A on the demo laptop. CP1 is now unblocked and needs A1 + A3 + minimal A6 (A), B1 + B2 (B) and C1 (C). |
