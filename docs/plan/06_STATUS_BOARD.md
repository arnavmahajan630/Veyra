# 06 — STATUS BOARD

Update your row after every phase (status, one line, report link). Status values: `todo`, `in-progress`, `blocked(<why>)`, `done`, `cut`.

| Phase | Owner | Status | Contracts | Summary | Report |
|---|---|---|---|---|---|
| S0 Bootstrap | all (run by A) | done | v1.1 | AC1–AC7 pass; stack idles at 2.89 GiB; 77 unit + 5 integration tests green. LLM runs on CPU (53–91 s/draft) — GPU unresolved, @C | [S0.md](reports/S0.md) |
| A1 Edge collectors (Vector) | A | done | v1.2 | 5/5 ACs pass; UUIDv7 + byte-accurate raw_len; T3 joins to one envelope; 60 s Kafka outage lossless; inventory reload verified | [A1.md](reports/A1.md) |
| A2 Ingest gateway (HTTP push) | A | todo | v1.3 | | |
| A3 Engine core + normalizer service | A | done | v1.3 | 5/5 ACs; 2732 EPS/core tier 1; byte-exact offsets incl. inside JSON; purity + cross-process determinism proven; transactional normalizer survives kill -9 | [A3.md](reports/A3.md) |
| A4 Tier 3, offsets, robustness | A | done | v1.3 | 5/5 ACs; unregistered sources now tier 3 with observables + byte offsets (goldens moved 4 -> 3); 50k fuzz cases, 0 exceptions; provenance_check enforces both halves; crash-loop journal survives process death | [A4.md](reports/A4.md) |
| A5 Shadow, replay, revisions | A | todo | v1.3 | | |
| A6 Router + Wazuh + throughput bench | A | todo | v1.3 | | |
| B1 Lineage indexer + ClickHouse | B | todo | v1.0 | | |
| B2 Archiver + vault segments | B | todo | v1.0 | | |
| B3 Integrity: Merkle, signing, immudb | B | todo | v1.0 | | |
| B4 Evidence API: verify + export | B | todo | v1.0 | | |
| B5 Tamper lab | B | todo | v1.0 | | |
| B6 Console pages: Lineage + Evidence | B | todo | v1.0 | | |
| B7 Demo engine + demo panel | B | todo | v1.0 | | |
| C1 Control API foundation | C | done | v1.4 | Merged (PR #2): auth/roles/tenant scoping, sources + keys, compacted `control` publisher, inventory writer, audit, SSE, seed/reset. Contract compiler (C2 task 1) pulled forward and merged with it. AC3–AC5 pass in unit tests; AC1/AC2 live halves wait for CP1 (normalizer, gateway) | [C1.md](reports/C1.md) |
| C2 Contract registry + compiler + lifecycle | C | in-progress | v1.4 | Code complete on branch `c2-c3-registry-drift` (not yet merged): golden runner, lint, `make contracts-test` (7/7 contracts), lifecycle with four-eyes, backtest, diff, replay jobs. AC1, AC3, AC5 pass; AC2/AC4 pass standalone, live halves need A5 + B (CP3). Plan-doc sync pending | [C2.md](reports/C2.md) |
| C3 Drift worker + library packs | C | in-progress | v1.4 | Code complete on branch `c2-c3-registry-drift` (not yet merged): drift worker (Drain3 0.9.11, persisted state), drift inbox with auto-resolve on promote, 5 library packs (76 goldens, all tier 1), `library_match`. AC1–AC4 pass standalone; live timing smoke pending | [C3.md](reports/C3.md) |
| C4 LLM drafter + provenance + bench | C | todo | v1.4 | Plan written. Needs the GPU vs `LLM_MODE=cache` decision (S0 REQUEST @C) before the bench | |
| C5 Console shell + design system + Overview/Sources | C | todo | v1.4 | Plan written and verified outside the repo (114 unit tests, build, Playwright smoke); not yet implemented in `console/` | |
| C6 Console: Onboarding + Contracts/Drift + Delivery/Audit | C | todo | v1.4 | Plan written; starts after C5 lands | |
| CP1 First light | all | todo | | | |
| CP2 Messy + evidence | all | todo | | | |
| CP3 Loop closed | all | todo | | | |
| CP4 Demo freeze | all | todo | | | |
| S2 Hardening + rehearsal + backup video | all | todo | | | |

## Checkpoint log

| Checkpoint | Date | Result | Notes |
|---|---|---|---|
| A4 | 2026-09-28 | PASS | Tier 3 live end to end: a messy unregistered line reaches `norm.uncategorized` as tier 3 with both IPs, the user and offsets that slice the raw bytes after Kafka. CP2 now needs A5 + A6 (A), B2-B4 (B), C2-C3 (C). |
| A3 | 2026-09-27 | PASS | Engine + normalizer live: syslog -> raw.linux -> norm.iam tier 1 with real Kafka coordinates. CP1 now needs only minimal A6 from track A, plus B1 + B2 (B) and C1 (C). |
| A1 | 2026-09-27 | PASS | Edge is live on both zones. CP1 still needs A3 + minimal A6 (A), B1 + B2 (B), C1 (C). |
| S0 | 2026-09-27 | PASS | Run solo by A on the demo laptop. CP1 is now unblocked and needs A1 + A3 + minimal A6 (A), B1 + B2 (B) and C1 (C). |
