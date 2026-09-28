# A5 — Shadow mode, backtest library, replay consumption, revisions

```
track: A   owner: A   status: todo
contracts: v1.4
depends_on: [A4, C2 (candidate in control msg; mock until then)]   unblocks: [CP3, Beat 4]
consumes: [IF-CONTROL (contract candidate), IF-ENVELOPE (+replay block), IF-SHADOW, IF-LINEAGE]
provides: [veyra_engine.backtest(), shadow records, revision semantics in IF-ULPF]
directories: [packages/veyra_engine/, services/normalizer/]
```

## Goal

Make contract changes safe and history repairable (v1 §11.3 canary/shadow, §7.4 replay):
- a candidate contract version runs alongside the active one without affecting output;
- a pure `backtest()` gives instant before/after results for the control plane;
- replayed events are re-emitted with the same `event_uid` and a higher `revision`.

## Design

**Candidate handling.** A `contract:<id>` control message may carry `candidate: {version, compiled}`. The engine keeps `active[id]` and `candidate[id]`. For events whose source maps to that contract:
- `normalize()` runs active (the output), then candidate (shadow only);
- emit an IF-SHADOW record: tiers of both, `changed_fields` (paths whose value differs or appears), `regressions` (paths present in active but missing in candidate, or a tier that got worse);
- the candidate output is **never** produced to `norm.*`.
- Budget: the candidate run counts against a separate budget. If exceeded, skip the shadow for that event and increment a metric.

**`backtest(active: CompiledContract|None, candidate: CompiledContract, envelopes: list[Envelope]) -> BacktestResult`.** A pure function. Result:
- `{n, tier_before: {1:..,2:..,3:..,4:..}, tier_after: {...}, upgraded: n, regressed: n, unchanged: n}`;
- `examples: [{event_uid, before_tier, after_tier, changed_fields, provenance_ok}]` (up to 20);
- `field_coverage: {ocsf_path: pct}`.

C2 and C4 call this in-process for the instant "8/8 tier 3 → 1" panel.

**Replay.** Messages on `replay.raw` are IF-ENVELOPE plus `replay: {job_id, revision, supersedes}`, with `supersedes = "<event_uid>@<prev_revision>"`. The control-api computes `revision` from ClickHouse.
- The normalizer processes them like raw, but sets `ulpf.revision`, `ulpf.supersedes` and `ulpf.replay=true`, and `lineage.replay_job_id`.
- Same transaction semantics.
- Replay uses the **active** contract at replay time.

**Idempotence.** A replay record re-processed after a crash yields the same revision number, so `norm_lineage` has no duplicate `(event_uid, revision)` (ReplacingMergeTree in B1).

## Tasks
- [ ] 1. Candidate support in `Engine` + control follower; shadow diff computation; IF-SHADOW production.
  <!-- synced from A3 --> `Engine.set_candidate` and `_resolve_contract` are in place: candidates are
  keyed by **contract id**, and resolution follows source -> active contract -> candidate, including a
  candidate that declares a source no active contract covers. The control follower already reads the
  `candidate` block from `contract:<id>` messages and calls `set_candidate`. What remains is running
  both and emitting IF-SHADOW.
- [ ] 2. `backtest()` + tests using the T3 corpus with a hand-written `authsrv@2`.
  <!-- synced from A4 --> Tier 3 changes what "improvement" means here: an unregistered source already
  produces observables and offsets, so the comparison is tier 3 -> tier 1 **plus field coverage**, not
  "tier 4 became tier 1". Also note `normalize` now enforces a per-event `Budget` at stage boundaries —
  a backtest over thousands of events should pass a generous limit (or none) rather than inherit the
  realtime one, or long events will be reported as `budget_exceeded` regressions that are not real.
  <!-- synced from A3 --> `authsrv@2` already exists as
  `packages/veyra_engine/tests/contracts/authsrv_v2.yaml`, and
  `test_authsrv_v2_upgrades_t3_to_tier_one` proves all 8 T3 events go tier 3 -> tier 1 with the
  attacker IP located in the raw bytes. `backtest()` has the real shape (tier histograms, upgraded /
  regressed / unchanged, examples); it still needs `field_coverage` and the richer examples A5 lists.
- [ ] 3. `replay.raw` consumption, revision fields, `lineage.replay_job_id`.
  <!-- synced from A2 --> Envelopes can now arrive with `custody="post_hoc"` (batch upload) and with
  `hec_meta` set (HTTP push). A replay must carry both through unchanged — re-stamping a batch-uploaded
  event as `realtime` would erase the one field that tells an analyst it was back-filled.
  <!-- synced from A3 --> The normalizer already consumes `replay.raw` alongside `^raw\..*` and carries
  `replay`, `revision` and `supersedes` from the envelope's replay block into `ulpf`, with
  `lineage.replay_job_id` filled. A5's work is the semantics (revision from ClickHouse, idempotence
  under crash), not the plumbing.
- [ ] 4. `tools/mock_replay.py` to produce replay messages until C2's replay job exists.
- [ ] 5. Metrics:
  - `veyra_shadow_events_total{contract}`
  - `veyra_shadow_regressions_total`
  - `veyra_replay_events_total{job}`

## Acceptance criteria
- [ ] AC1: Candidate `authsrv@2` published with T3 traffic flowing → `shadow` shows candidate tier 1 vs active tier 3; `norm.*` still shows tier 3 only.
- [ ] AC2: `backtest(authsrv@1, authsrv@2, 8 T3 envelopes)` → `upgraded=8`, `regressed=0`, in < 200 ms.
- [ ] AC3: Replay 8 events → 8 `norm.iam` events with `revision=2`, `supersedes` set, tier 1, same `event_uid`s.
- [ ] AC4: Kill the normalizer mid-replay → no duplicate `(event_uid, revision)` after recovery.

## Implementation notes
_(filled after execution)_
