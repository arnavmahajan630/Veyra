# Track C — Control plane & console (Person C)

```
contracts: v1.1
```

## Mission

Everything humans touch, and everything that governs change:
- tenants, sources and keys;
- Log Contracts, their compiler and their lifecycle (draft → testing → canary → active, with four-eyes approval);
- drift detection;
- LLM-assisted drafting that is provably grounded in real bytes;
- the console shell, design system and most pages.

v1 refs: §11 (control plane), §12 (console), ADR-04 (LLM off the hot path).

## You own

**Directories:**
- `packages/veyra_contracts/`
- `services/{control_api,drift_worker}/`
- `contracts-repo/`
- `console/`: shell, design system, and the pages Overview, Sources, Onboarding, Contracts, Drift, Delivery, Audit. B owns `pages/{lineage,evidence,demo}`.
- `tools/bench/llm_bench.py`, `bench/llm_golden/`

**Interfaces:** IF-CONTRACT-YAML, IF-CONTRACT-COMPILED, IF-CONTROL, IF-INVENTORY (writer side), IF-API-CONTROL, IF-LLM-DRAFT, IF-AUDIT (producer side).

## Phases

| Phase | Title | Depends on | Feeds |
|---|---|---|---|
| C1 | Control API foundation: auth, tenants, sources, keys, control publish, inventory, audit, SSE, seed, internal endpoints | S0 | CP1 |
| C2 | Contract registry: YAML models, compiler, golden tests, git, lifecycle, four-eyes, canary, backtest, replay jobs | C1, A3 (engine) | CP2, CP3 |
| C3 | Drift worker (Drain3) + library packs + library matching | C2, A4 (tokens, mask) | CP3 |
| C4 | LLM drafter: token-ref drafting, provenance, heuristic fallback, cache, onboarding analyze, bench | C2, C3, A4 | CP3 |
| C5 | Console shell + design system + RawHighlighter + Overview + Sources | S0, B1 fixtures | CP2 |
| C6 | Console pages: Onboarding wizard, Contracts, Drift & draft review, Delivery, Audit | C2–C5 | CP3 |

**Parallel pairs:**

| Pair | Why it works |
|---|---|
| C1 with C5 | Backend vs frontend |
| C2 with C5 (continuing) | Backend vs frontend |
| C3 with C4 | Separate modules |

Start C5 on day one against fixtures. The console is what judges look at most.

C carries the widest surface. **Cut order inside C** (per `01_TEAM_GUIDE.md` §9):
1. Delivery and Audit page polish;
2. the Hindi toggle;
3. live shadow counts (keep the backtest).

## Consumers of your work

| Track | Uses |
|---|---|
| A | Compiled contracts, keys, sources, routes and vocab via the `control` topic; the inventory CSV |
| B | `/internal/reset` and `/internal/demo/last-key` (B7); the design system and `RawHighlighter` (B6); the replay job builder calls B's raw fetch |
| Everyone | The console shell: routing, auth, SSE hooks, hotkey registry, i18n |
