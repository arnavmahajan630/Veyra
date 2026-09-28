# C3 — Drift worker (Drain3), drift inbox, library packs, library matching

```
track: C   owner: C   status: in-progress
contracts: v1.4
depends_on: [C2, A4 (template_sig, mask, extract_tokens)]   unblocks: [CP3, C4, Beat 4]
consumes: [IF-DLQ, IF-TEMPLATE-SIG, IF-API-CONTROL (/internal/drift)]
provides: [drift items, library packs, library_match()]
directories: [services/drift_worker/, services/control_api/ (drift module), ../contracts-repo/library/ (separate repository)]
```

## Goal

Notice new message shapes automatically (v1 §11.1) and turn them into reviewable **drift items** within seconds. Offer a library of ready contracts so common sources onboard without any drafting.

## Design

### Drift worker
- Consumes `dlq`, group `drift-worker`.
- **Primary grouping is deterministic:** `(source_id, template_sig)` (IF-TEMPLATE-SIG). Counts, first/last seen and up to 5 `text_masked` samples (distinct) per group.
- **Drain3** runs per source (one `TemplateMiner` per source, persisted to `data/state/drain3/<source>.json`) on `text_masked`. It produces a human-readable `drain_template` with `<*>` wildcards for display and for the LLM. When several sigs land in the same Drain cluster, record them as `related_sigs` on the drift item, so the drafter can cover them all in one contract template.
- **Emission rule:** a group becomes (or updates) a drift item when `count ≥ DRIFT_MIN_CLUSTER`, or immediately when a force flush is requested (`POST /flush` on the worker, used by B7's stage 4 fallback). Upserts use `POST control-api /internal/drift`, debounced to one update per group per 2 s.
- **Suppression:** sigs already covered by an active or canary contract version (the control-api checks by running the candidate regexes on the samples) are closed automatically with state `resolved_by:<contract@v>`.
- **Metrics:** `veyra_drift_groups`, `veyra_drift_items_emitted_total`.

### Drift inbox (control-api)
- `drift_items` table: `drift_id`, `source_id`, `tenant_id`, `template_sig`, `related_sigs`, `drain_template`, `count`, `first_seen`, `last_seen`, `samples_masked`, `state` (`open | drafting | draft_ready | resolved | dismissed`), `draft_id`.
- Endpoints per IF-API-CONTROL (`GET /drift`, `POST /drift/{id}/draft`, plus `POST /drift/{id}/dismiss`).
- SSE `drift` events on create/update.
- **Auto-draft setting** `VEYRA_DRIFT_AUTODRAFT=1`: when an item is created, start a draft in the background (C4), so the drift card in Beat 4 already has a draft by the time the presenter opens it.

### Library packs (`library/` in the contract registry, `../contracts-repo`)
- `linux_sshd.yaml`, `acme_ngfw_cef.yaml`: imported from A3's fixtures, with their samples and expected files.
- `generic_cef.yaml`: a CEF header + extension → Network Activity with common keys (`src`, `dst`, `spt`, `dpt`, `act`, `suser`).
- `generic_leef.yaml`.
- `nginx_access.yaml`: combined log format → HTTP Activity.
- Each has samples and golden tests (≥ 10 samples each).

**`library_match(samples) -> [{contract_id, tier1_pct, tier2_pct}]`:** compile each library contract, run the samples through `veyra_engine`, and return the ranked matches. A match counts when `tier1_pct ≥ 0.8`. Used by C4's onboarding analyze: a library match skips LLM drafting and proposes "use library pack X (cloned into your tenant)".

## Tasks
- [x] 1. The drift worker: grouping, Drain3 per source with persistence, emission, debounce, force flush.
- [x] 2. Drift tables + endpoints + SSE + auto-close of covered sigs.
- [x] 3. Library packs with samples and golden tests (run by `make contracts-test`).
- [x] 4. `library_match()` + tests (sshd samples → `linux_sshd`, ~100%; authsrv samples → no match).
- [ ] 5. Integration: push T3 ×8 → a drift item within 10 s, with `drain_template` like `user=<*> FAILED login from <*> via <*> attempts:<*>`.

## Acceptance criteria
- [ ] AC1: T3 ×8 → exactly one drift item for `src_authsrv_01` with count 8 and 5 distinct masked samples, created in < 10 s.
- [x] AC2: After `authsrv@2` is promoted, the item auto-resolves.
- [x] AC3: Restarting the drift worker loses no groups (Drain3 state + counts are recovered; counts are rebuilt from the DLQ with the consumer group reset to the retention window on first boot, or persisted; choose one and document it).
- [x] AC4: `library_match` identifies the sshd and CEF samples correctly and rejects authsrv.

## Settings
`VEYRA_DRIFT_MIN_CLUSTER`, `VEYRA_DRIFT_AUTODRAFT` (1 in the demo), `VEYRA_DRIFT_DEBOUNCE_MS` (2000).

## Implementation notes
<!-- synced from C3 --> Code complete; merged on `main` as PR #3 (2026-09-28). Report in `reports/C3.md`. Live timing smoke still open, so the phase stays in progress.
- **AC1:** grouping and upsert pass standalone; task 5's live timing (< 10 s) is still to run.
- **AC3 strategy:** persisted counts (`data/state/drift/groups.json`) beside the Drain3 state, with offsets committed after each checkpoint.
- **Drain3:** 0.9.11, masking `key=`/`key:` values and IPv4s first.
- **Resolution:** an item resolves when the **active** version covers it, not the canary.
- **Payload:** `/internal/drift` also carries `related_sigs` and `sample_event_uids`.
- **Library packs:** in `library/` with tenant `t_library`: linux_sshd (all 24 corpus shapes), acme_ngfw_cef, generic_cef, generic_leef, nginx_access, 76 goldens, all tier 1.
- **Generic packs** map only the fields every event carries.
- **Reset:** control-api's `/internal/reset` also calls the drift worker's `/reset`.
- **Open:** whether the seeded `linux_sshd` should become the library version.
