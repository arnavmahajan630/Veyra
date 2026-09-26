# 01 — TEAM GUIDE: three people, many agents, one demo

## 1. Roles

| Person | Track | Owns (directories) | Also owns |
|---|---|---|---|
| **A — Data plane** | `track-A-dataplane/` | `edge/`, `packages/veyra_engine/`, `services/{ingest_gateway,normalizer,router}/`, `wazuh/` | IF-ENVELOPE, IF-ENGINE-LIB, IF-ULPF, IF-NORM-EVENT, IF-ROUTES, IF-WAZUH, IF-TEMPLATE-SIG |
| **B — Evidence & lineage** | `track-B-evidence/` | `packages/{veyra_evidence,veyra_lineage}/`, `services/{archiver,integrity,lineage_indexer,evidence_api,demo_engine}/`, `demo/`, `console/src/pages/{lineage,evidence,demo}/` | IF-CHAIN, IF-SEGMENT, IF-MERKLE, IF-SIGNED-ROOT, IF-KEYPROVIDER, IF-CH-SCHEMA, IF-API-EVIDENCE, IF-API-DEMO |
| **C — Control & console** | `track-C-control-console/` | `packages/veyra_contracts/`, `services/{control_api,drift_worker}/`, `contracts-repo/`, `console/` (shell, design system, other pages) | IF-CONTRACT-YAML, IF-CONTRACT-COMPILED, IF-CONTROL, IF-INVENTORY, IF-API-CONTROL, IF-LLM-DRAFT |
| **Shared** | `shared/` | `compose/`, `profiles/`, `packages/veyra_common/`, `Makefile`, `Caddyfile`, CI | IF-TOPICS, IF-PORTS, IF-NAMING, IF-ENV, IF-VERSIONS |

**Ownership rule:** you may *read* anything, but *change* only what you own. To change something another track owns, open a changelog request (§6.3) or pair with the owner.

The person who will present the demo (usually whoever speaks best) also owns `04_DEMO_SCRIPT.md` from S2 onward.

## 2. The shape of the build

```
S0 bootstrap (all 3 together; everyone blocked until done)
   │
   ├── A1 edge ── A2 gateway ── A3 engine core ── A4 tier3+offsets ── A5 shadow+replay ── A6 router+wazuh
   ├── B1 lineage/CH ── B2 archiver ── B3 integrity ── B4 verify/export ── B5 tamper lab ── B6 UI pages ── B7 demo engine
   └── C1 control-api ── C2 contracts ── C3 drift ── C4 LLM drafter ── C5 console shell ── C6 console pages
          │                                   │                         │
        CP1 first light ──────────────────── CP2 messy + evidence ──── CP3 loop closed ──── CP4 demo freeze (S1)
                                                                                              │
                                                                                         S2 hardening + rehearsal
```

### 2.1 What each checkpoint needs (see `shared/S1_integration_checkpoints.md`)

| Checkpoint | Needs from A | Needs from B | Needs from C |
|---|---|---|---|
| **CP1 first light**: a syslog line reaches Wazuh; a segment is sealed; ClickHouse has rows | A1, A3 (tier 1/4), A6 (minimal) | B1, B2 | C1 (control publish with a seeded contract) |
| **CP2 messy + evidence**: tier 3 in Wazuh; verify green; console overview live | A4 | B3, B4 | C2, C5 |
| **CP3 loop closed**: drift → draft → approve → promote → replay → Wazuh revision; onboarding; tamper red | A2, A5 | B5, B6 | C3, C4, C6 |
| **CP4 demo freeze**: scenario runs 10/10 from reset | — | B7 | — |

Phases *within* a track are ordered. Tracks run in parallel. Mocks make that possible:
- S0 ships stubs for `veyra_engine` (tier 4 only), a fake raw generator, a fake norm generator, and seeded contracts.
- C can build the UI before A's engine is finished; B can index before the normalizer exists.

**Optimize for checkpoints, not phases.** If B is ahead and C is behind on CP2, B helps C (pair or take a sub-task), not B's next phase.

## 3. Day zero: S0 together

Do `shared/S0_bootstrap.md` with all three people in one room or one call. It creates:
- the repo, compose base and profiles;
- `veyra_common` and the stubs;
- the demo corpus;
- CI;
- the pinned versions.

Nobody starts a track until S0's acceptance checks pass on **the demo laptop**.

## 4. Agent workflow (per phase)

Each phase file is written so that one agent session can execute it end-to-end. The human's job is to steer, review and integrate.

### 4.1 The loop

1. **Prepare.** `git pull`. Read `05_CHANGELOG.md` for new `ACTION REQUIRED` items for your track, and handle them first.
2. **Kick off.** Start an agent with `templates/AGENT_KICKOFF.md`, filled in with the phase ID. Context to give it:
   - `00_MASTER.md`
   - the `02_CONTRACTS.md` sections the phase lists
   - the phase file
   - your track's `X00_TRACK.md`
   - the phase reports of phases it depends on

   Nothing else. Small, precise context beats big context.
3. **Plan check.** The agent restates the plan and lists any contract gaps *before* coding. You approve or correct in under 5 minutes.
4. **Execute.** The agent works on a branch `x<n>-<slug>` in its own **git worktree**. Tests are written alongside the code, not after.
5. **Verify.** Run the phase's acceptance checks yourself, not just the agent's claim. At least one check must be observed by a human, e.g. "see the event in Wazuh".
6. **Report and sync.** The agent writes `reports/<phase-id>.md` (template) and runs `templates/PLAN_SYNC.md` (§6).
7. **Merge.** PR → CI green → merge to `main`. Rebase often; merges should be small and frequent.

### 4.2 Running two agents at once (recommended)

Each person can run **two agent sessions in parallel** on separate worktrees, as long as they touch different directories. Good pairs:

| Person | Pair |
|---|---|
| A | A3 engine core **and** A1 edge config |
| B | B2 archiver **and** B1 ClickHouse indexer |
| C | C1/C2 backend **and** C5 console shell |

```bash
git worktree add ../veyra-a3 -b a3-engine-core
git worktree add ../veyra-a1 -b a1-edge
```

Never run two agents in the same directory tree. Never let an agent edit another track's directories.

### 4.3 Rules every agent must follow (they're in the kickoff prompt too)

1. Interfaces come only from `02_CONTRACTS.md`. If something is missing or wrong, **stop and propose** a change (additive: proceed and log it; breaking: ask the human).
2. No magic numbers. Every limit or interval goes through `Settings` with a profile default.
3. Every new record type gets a Pydantic model in the owning package and a round-trip test.
4. Every service: `/healthz`, `/metrics`, structured JSON logs, graceful shutdown (flush, commit, close).
5. Every consumer survives malformed input: validate, send to DLQ with a reason, continue. Crashes are bugs.
6. Tests: unit tests for logic, test vectors for algorithms, and at least one integration test against the real compose service for Kafka, ClickHouse and immudb code paths.
7. Don't upgrade pinned versions (IF-VERSIONS) without a changelog entry.
8. Finish with the phase report and plan sync. A phase without a report is not done.

## 5. Definition of Done (per phase)

- [ ] All tasks in the phase file are checked or explicitly deferred with a reason.
- [ ] Acceptance criteria pass, and a human observed at least one.
- [ ] `make test` and `make lint` are green; the phase's integration test is green against compose.
- [ ] Memory limits respected on the laptop profile (`docker stats` snapshot in the report).
- [ ] Report written; status board updated; plan sync done.
- [ ] The phase's "Demo relevance" item has been checked visually.

## 6. Keeping plans alive (the sync protocol)

Plans *will* change once code meets reality. The protocol keeps everyone's agents working from the truth.

### 6.1 After every phase: always

1. Write `reports/<phase-id>.md`, including an **"Interfaces touched"** section listing `IF-*` IDs, each marked "unchanged", "additive" or "breaking".
2. Update the phase file's **"Implementation notes"** section with what was actually built, deviations and gotchas.
3. Update `06_STATUS_BOARD.md`: status, one-line summary, report link.
4. Update your own track's **downstream phase files** with anything learned: changed file paths, better approaches, removed tasks, new risks.

### 6.2 If any `IF-*` changed

1. Edit `02_CONTRACTS.md` and bump the version (§0 rules).
2. Add a `05_CHANGELOG.md` entry: what changed, why, the IDs touched, and `ACTION REQUIRED @A/@B/@C` with the exact action.
3. `grep -rn "IF-<ID>" docs/plan/` and patch every phase file that references it, including other tracks' files. You may edit another track's *plan files* for interface propagation only. Mark each edit with `<!-- synced from <phase-id> -->`.
4. Update the `contracts: vX.Y` header in every patched file.
5. If breaking, tell the affected owners directly as well. The changelog is the record, not the notification.

### 6.3 When you need something from another track

Add a changelog entry of type `REQUEST @X: <what> for <phase>`. The owner answers in the changelog by accepting (with a target phase) or proposing an alternative. For urgent items, message them too.

### 6.4 Staleness check (start of every work block)

`make plan-check` (added in S0) greps every plan file's `contracts: vX.Y` header and lists files older than the current contract version. Stale files that your next phase depends on must be synced before you start.

## 7. Integration discipline

- `main` must always boot: `make up && make e2e-smoke` passes. A red `main` is everyone's top priority.
- Integrate at every checkpoint using `shared/S1_integration_checkpoints.md`. All three people are present for the first run of each checkpoint.
- **Contract tests:** each owning package publishes JSON fixtures in `packages/<pkg>/fixtures/`, and consumers test against those fixtures. When a producer changes a fixture, consumer CI fails, which is intended.

## 8. Communication

| When | What |
|---|---|
| Start of each work block (10 min) | Each person: done, next, blocked. Read the changelog together. |
| Each checkpoint | Run the checkpoint script together; decide cuts if behind. |
| Any breaking change | Direct message plus changelog. |
| One shared channel | Post phase-done messages with the report link. |

## 9. When you're behind: the cut list

Cut in this order, and never cut anything above the line.

| # | Keep or cut |
|---|---|
| 1 | **Never cut:** messy → Tier 3 → Wazuh; drift → draft → approve → replay; field highlight; verify green; tamper red; reset |
| 2 | Cut first: Hindi toggle, audit page, delivery page polish |
| 3 | Then: OpenBao profile, observability profile |
| 4 | Then: live shadow (keep the backtest), partner masked route |
| 5 | Then: the batch upload endpoint (keep HEC) |
| 6 | Then: onboarding via LLM (use a library-match or heuristic draft on stage; keep the wizard UI) |

## 10. Hardware switching

Everything is profile-driven (`03_INFRA_PROFILES.md`). Switching machines means:
1. copy the repo;
2. `make up PROFILE=<laptop|mac|workstation>`;
3. `make bench-llm` to confirm the model;
4. `make demo-reset`.

If you do any tuning on the new machine, commit it to that profile file, never to code.
