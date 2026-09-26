# VEYRA Demo — Plan Folder

This folder is the single source of truth for building the VEYRA SIH demo. Humans and coding agents both work from it.

## Reading order

| # | File | Who reads it | When |
|---|---|---|---|
| 1 | `00_MASTER.md` | Everyone, every agent session | First, always |
| 2 | `01_TEAM_GUIDE.md` | Every human | Before S0 |
| 3 | `02_CONTRACTS.md` | Every agent (relevant sections only) | Before touching any interface |
| 4 | `03_INFRA_PROFILES.md` | Whoever touches compose, limits or the LLM | S0 and when switching hardware |
| 5 | `04_DEMO_SCRIPT.md` | Everyone | Before S0, then again at S2 |
| 6 | `06_STATUS_BOARD.md` | Everyone | Start of every work block |
| 7 | `05_CHANGELOG.md` | Everyone | Start of every work block |
| 8 | Your track folder (`track-*/X00_TRACK.md`, then phase files) | Track owner + their agents | Per phase |

## Folder map

```
00_MASTER.md               whole-project context: what, why, architecture, decisions
01_TEAM_GUIDE.md           how 3 people + agents work in parallel without collisions
02_CONTRACTS.md            every shared interface, with IDs (IF-*); the ONLY place interfaces are defined
03_INFRA_PROFILES.md       laptop / mac / workstation profiles; every scalable knob
04_DEMO_SCRIPT.md          the 3-minute demo, beat by beat, with fallbacks
05_CHANGELOG.md            cross-track change log; contract bumps; ACTION REQUIRED notices
06_STATUS_BOARD.md         one-line status per phase; updated after every phase
reference/spec_vectors.py  executable reference for hashing/signature algorithms (test vectors)
templates/                 agent kickoff prompt, phase report, plan-sync prompt, phase file template
shared/                    S0 (bootstrap, all together), S1 (integration checkpoints), S2 (demo hardening)
track-A-dataplane/         Person A: edge, gateway, normalizer engine, router, Wazuh
track-B-evidence/          Person B: lineage index, vault, integrity, verify, evidence/lineage UI, demo engine
track-C-control-console/   Person C: control API, contracts, drift, LLM drafting, console shell + pages
reports/                   phase reports written by agents after each phase (<phase-id>.md)
```

## The one rule

**Plans are living documents.** After every phase, the executing agent writes a report, updates the status board, and propagates any interface or design change into `02_CONTRACTS.md`, `05_CHANGELOG.md` and every affected plan file. See `01_TEAM_GUIDE.md` §6 and `templates/PLAN_SYNC.md`.
