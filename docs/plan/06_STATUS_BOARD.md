# 06 — STATUS BOARD

Update your row after every phase (status, one line, report link). Status values: `todo`, `in-progress`, `blocked(<why>)`, `done`, `cut`.

| Phase | Owner | Status | Contracts | Summary | Report |
|---|---|---|---|---|---|
| S0 Bootstrap | all (run by A) | done | v1.1 | AC1–AC7 pass; stack idles at 2.89 GiB; 77 unit + 5 integration tests green. LLM runs on CPU (53–91 s/draft) — GPU unresolved, @C | [S0.md](reports/S0.md) |
| A1 Edge collectors (Vector) | A | done | v1.2 | 5/5 ACs pass; UUIDv7 + byte-accurate raw_len; T3 joins to one envelope; 60 s Kafka outage lossless; inventory reload verified | [A1.md](reports/A1.md) |
| A2 Ingest gateway (HTTP push) | A | done | v1.4 | 5/5 ACs; HEC raw/event/batch with per-source keys from `control`, revocation live in <2 s, quotas enforced, 503-never-200 durability; envelopes byte-identical to the edge's parity vectors | [A2.md](reports/A2.md) |
| A3 Engine core + normalizer service | A | done | v1.3 | 5/5 ACs; 2732 EPS/core tier 1; byte-exact offsets incl. inside JSON; purity + cross-process determinism proven; transactional normalizer survives kill -9 | [A3.md](reports/A3.md) |
| A4 Tier 3, offsets, robustness | A | done | v1.3 | 5/5 ACs; unregistered sources now tier 3 with observables + byte offsets (goldens moved 4 -> 3); 50k fuzz cases, 0 exceptions; provenance_check enforces both halves; crash-loop journal survives process death | [A4.md](reports/A4.md) |
| A5 Shadow, replay, revisions | A | todo | v1.4 | | |
| A6 Router + Wazuh + throughput bench | A | todo | v1.4 | | |
| B1 Lineage indexer + ClickHouse | B | todo | v1.0 | | |
| B2 Archiver + vault segments | B | todo | v1.0 | | |
| B3 Integrity: Merkle, signing, immudb | B | todo | v1.0 | | |
| B4 Evidence API: verify + export | B | todo | v1.0 | | |
| B5 Tamper lab | B | todo | v1.0 | | |
| B6 Console pages: Lineage + Evidence | B | todo | v1.0 | | |
| B7 Demo engine + demo panel | B | todo | v1.0 | | |
| C1 Control API foundation | C | done | v1.4 | Merged (PR #2): auth/roles/tenant scoping, sources + keys, compacted `control` publisher, inventory writer, audit, SSE, seed/reset. Contract compiler (C2 task 1) pulled forward and merged with it. AC3–AC5 pass in unit tests; AC1/AC2 live halves wait for CP1 (normalizer, gateway) | [C1.md](reports/C1.md) |
| C2 Contract registry + compiler + lifecycle | C | in-progress | v1.4 | Merged (PR #3): golden runner, lint, `make contracts-test` (7/7 contracts), lifecycle with four-eyes, backtest, diff, replay jobs. AC1 and AC5 pass; AC3 API half passes; AC2/AC4 pass standalone, live halves need A5 + B (CP3) | [C2.md](reports/C2.md) |
| C3 Drift worker + library packs | C | in-progress | v1.4 | Merged (PR #3): drift worker (Drain3 0.9.11, persisted state), drift inbox with auto-resolve on promote, 5 library packs (76 goldens, all tier 1), `library_match`. AC2–AC4 pass; AC1 grouping passes, live timing smoke still open | [C3.md](reports/C3.md) |
| C4 LLM drafter + provenance + bench | C | in-progress | v1.4 | Merged (PR #5). Drafter pipeline, drift drafts, onboarding analyze (SSE), cache mode on the laptop. Bench runner and `llm-cache-seed` landed; live p95 and the two-model report (AC2, AC5) deferred until a GPU | [C4.md](reports/C4.md) |
| C5 Console shell + design system + Overview/Sources | C | in-progress | v1.4 | Merged (PR #6): `console/` with design tokens + contrast check, EN/HI, hotkeys, SSE with backoff, RawHighlighter + thread (B6 APIs frozen), shell with demo user switch, Overview, Sources + drawer + silent-source alert, mock mode. 126 unit tests, Playwright smoke. AC3–AC5 pass; AC1/AC2 pass on mocks, live halves need B1/B4 (CP2) | [C5.md](reports/C5.md) |
| C6 Console: Onboarding + Contracts/Drift + Delivery/Audit | C | in-progress | v1.5 | Code complete on branch `c6-console-pages` (not yet merged): /onboard (streamed analysis, compact review, four-eyes, key), /contracts (history, diff, confirmed lifecycle), /drift (inbox, full review with the thread, submit → approve → promote → replay, 5 s cache fallback), /delivery, /audit (CSV); control-api `GET /routes`, `POST /onboarding/use-library`, re-draft while drafting. 191 unit tests + Beat 2/4 Playwright flows in mock mode; walkthrough refinements done. AC3/AC4 pass; AC1/AC2 pass on mocks, real timing at rehearsal (CP3); AC5 keyboard walkthrough in rehearsal | [C6.md](reports/C6.md) |
| CP1 First light | all | todo | | | |
| CP2 Messy + evidence | all | todo | | | |
| CP3 Loop closed | all | todo | | | |
| CP4 Demo freeze | all | todo | | | |
| S2 Hardening + rehearsal + backup video | all | todo | | | |

## Checkpoint log

| Checkpoint | Date | Result | Notes |
|---|---|---|---|
| A2 | 2026-09-28 | PASS | Push ingest is live: an API key issued on `control` lets a shipper POST to :8088 and land a stamped envelope on `raw.custom`. CP3's onboarding beat now has a real endpoint; still needs C1's key issuance UI (C) and A5. |
| A4 | 2026-09-28 | PASS | Tier 3 live end to end: a messy unregistered line reaches `norm.uncategorized` as tier 3 with both IPs, the user and offsets that slice the raw bytes after Kafka. CP2 now needs A5 + A6 (A), B2-B4 (B), C2-C3 (C). |
| A3 | 2026-09-27 | PASS | Engine + normalizer live: syslog -> raw.linux -> norm.iam tier 1 with real Kafka coordinates. CP1 now needs only minimal A6 from track A, plus B1 + B2 (B) and C1 (C). |
| A1 | 2026-09-27 | PASS | Edge is live on both zones. CP1 still needs A3 + minimal A6 (A), B1 + B2 (B), C1 (C). |
| S0 | 2026-09-27 | PASS | Run solo by A on the demo laptop. CP1 is now unblocked and needs A1 + A3 + minimal A6 (A), B1 + B2 (B) and C1 (C). |
