# C2 — Contract registry, compiler, golden tests, lifecycle, four-eyes, canary, backtest, replay jobs

```
track: C   owner: C   status: todo
contracts: v1.4
depends_on: [C1, A3 (veyra_engine real; stub OK to start)]   unblocks: [CP2, CP3, C3, C4, C6, A5]
consumes: [IF-CONTRACT-YAML, IF-OCSF-SUBSET, IF-ENGINE-LIB, IF-API-EVIDENCE (raw fetch, template events), IF-TOPICS (replay.raw)]
provides: [IF-CONTRACT-COMPILED, IF-API-CONTROL (contracts, replay), IF-CONTROL (contract:* with candidate)]
directories: [packages/veyra_contracts/, services/control_api/ (contracts, replay modules), ../contracts-repo (separate repository)]
```

## Goal

Turn Log Contracts into governed, versioned, tested, deterministic parse plans, with v1's Pack CI and lifecycle (§11.2–11.3):
- YAML is validated against the OCSF subset;
- patterns compile to RE2;
- golden tests run through the *same* `veyra_engine` the normalizer uses;
- every version is a git commit;
- promotion requires a second person;
- candidates run in canary/shadow;
- replay jobs re-process history with the new version.

## Design

### `veyra_contracts` package
- **`models.py`:** Pydantic models mirroring IF-CONTRACT-YAML exactly. Unknown keys are an error. Field paths are validated against the IF-OCSF-SUBSET catalogue (`catalogue.py`, generated from the vendored OCSF subset schema A3 committed). Class and activity names map to uids. `const` values are validated against the enums.
- **`compiler.py`:** `compile(yaml_text) -> CompiledContract` (IF-CONTRACT-COMPILED).
  - The pattern tokenizer handles `<name[:type]>`, `<*>` and literal escaping; whitespace runs become `\s+`; the result is anchored.
  - Every regex is compiled with `re2` at compile time. Failure → a `ContractError` with line/column.
  - Map entries resolve to `{ocsf_path, kind, ref|value}`.
  - `compiler_version` is stamped.
  - Deterministic: the same YAML gives byte-identical compiled JSON (sorted keys). Test it.
- **`golden.py`:** `run_golden(compiled, tests_dir) -> GoldenReport`. For each `tests[]` pair:
  1. build an envelope from the sample file with `veyra_common.envelope.stamp`, using a fixed `received_time` from the test file header;
  2. `veyra_engine.Engine.normalize`;
  3. compare the output with the expected JSON, ignoring `ulpf.event_uid`, `ulpf.raw_ref` and `engine_version`.

  Diffs are printed as a path list.
- **`lint.py`:**
  - templates that can never match (shadowed by an earlier, broader template: test each template's samples against earlier regexes);
  - `required` paths not produced by any template;
  - PII paths not mapped.

### Registry in control-api
- **Storage:** YAML in the contract registry repository (`VEYRA_CONTRACTS_REPO`, default `../contracts-repo`) as `<tenant>/<id>.yaml`, committed with GitPython (or `git` via subprocess), author = the session user. Version N = the `version:` field; the commit sha is stored in `contract_versions.git_commit`. The seed commit is tagged `seed` (used by reset).
- **Tables:**
  - `contracts(id, tenant_id, active_version, canary_version)`;
  - `contract_versions(contract_id, version, state, yaml, compiled_json, author, approved_by, golden_report_json, backtest_json, git_commit, created_at, draft_id)`.
- **Lifecycle transitions** (each audited):

| Transition | Rule |
|---|---|
| `draft → testing` | On submit: compile + lint + golden. Failure returns to draft with the report. |
| `testing → canary` | Automatic if golden passes. Publish `contract:<id>` with `candidate`. Run the **backtest** immediately and store it. |
| `canary → active` | Needs `approve` by a `pack_approver` whose id ≠ the author (**403 otherwise**), then `promote`. Publish `contract:<id>` with the new compiled active and `candidate: null`; the old active becomes `retired`. |
| `rollback {to_version}` | The target becomes active; publish. Also needs an approver. |

  Only one canary per contract. A new submit replaces the canary (the old one → `retired` with reason `superseded`).
- **Backtest** (`POST /contracts/{id}/versions/{v}/backtest`, and automatic at canary):
  1. Fetch up to `VEYRA_BACKTEST_MAX` recent events for the contract's sources: all DLQ template sigs, plus a sample of tier 1 events for regression detection, via `GET /api/lineage/templates/{sig}/events` and raw via B4 `fetch_raw` (batch endpoint if B4 provides one; request it via the changelog if needed).
  2. Run `veyra_engine.backtest(active, candidate, envelopes)` in-process.
  3. Store and return the result.

  Target < 1 s for 200 events.
- **Diff** (`GET /contracts/{id}/diff?from=&to=`): a unified YAML diff plus a semantic diff (templates added/removed/changed; map changes per path).
- **Replay jobs** (`POST /replay`):
  1. Resolve the event set: by `template_sigs` and `source_id` from ClickHouse (via B's query endpoint), limited by `VEYRA_REPLAY_MAX`.
  2. For each event: get the current max revision (from lineage detail/bulk endpoint), fetch the raw envelope, and publish to `replay.raw` with `replay: {job_id, revision: max+1, supersedes: "<uid>@<max>"}`.
  3. The job record tracks `total/published/normalized`. `normalized` is polled from ClickHouse `norm_lineage WHERE replay_job_id = …`.
  4. SSE `replay` events report progress; state `done` when normalized == total or after a timeout.

## Tasks
- [ ] 1. models + catalogue + compiler + determinism tests + compile-error messages with locations.
- [ ] 2. golden runner + lint; run them on A3's library contracts (`linux_sshd`, `acme_ngfw_cef`).
- [ ] 3. The git-backed registry, contract tables, the seed tag.
- [ ] 4. Lifecycle endpoints, four-eyes enforcement, control publishing with candidates.
- [ ] 5. Backtest integration (with B4 raw fetch; use corpus envelopes as a fallback in tests).
- [ ] 6. Diff endpoint (YAML + semantic).
- [ ] 7. Replay jobs + progress + SSE.
- [ ] 8. `make contracts-test`: runs compile + lint + golden for every contract in the contract registry (`CONTRACTS_REPO`, default `../contracts-repo`; CI must check that repository out beside this one).

## Acceptance criteria
- [ ] AC1: The library contracts compile and pass golden tests. Compiled JSON is byte-identical across two runs.
- [ ] AC2: Submitting `authsrv@2` with a T3 template → canary; the normalizer emits shadow records (with A5); the backtest shows 8/8 upgraded.
- [ ] AC3: Approve by the author → 403. Approve by the approver → 200. Promote → the normalizer uses `@2` within 1 s.
- [ ] AC4: Replay of 8 events → the job reaches `done` with normalized = 8; revision 2 is visible in lineage.
- [ ] AC5: Rollback to `@1` works and is audited.

## Settings
`VEYRA_BACKTEST_MAX` (200), `VEYRA_REPLAY_MAX` (10000), `VEYRA_CONTRACTS_REPO`.

## Implementation notes
_(filled after execution)_
