# 05 — CHANGELOG (cross-track)

Newest entries on top. Every entry uses the format below. Handle `ACTION REQUIRED` items addressed to you before starting new work, then tick them.

```
## <YYYY-MM-DD HH:MM> — <phase-id> — <TYPE>  (contracts vX.Y → vX.Z)
TYPE ∈ {CONTRACT-ADDITIVE, CONTRACT-BREAKING, CLARIFICATION, REQUEST, DECISION, CUT, VERSION-PIN}
What:     <one or two lines>
Why:      <one line>
IDs:      IF-..., IF-...
Files patched: <list of plan files updated in this sync>
ACTION REQUIRED:
  - [ ] @A <exact action>
  - [ ] @B <exact action>
  - [ ] @C <exact action>
```

---

## 2026-10-02 22:10 — S2 + C4 — DECISION + REQUEST  (contracts v1.5, no bump)
TYPE: DECISION
What:     `./veyra.sh up` now lets the reviewer choose which AI drafts contracts:
          `--drafter ollama|laya` (or `--laya`). With a terminal and no flag it asks once and
          remembers the answer; with `--yes` or no terminal it uses `ollama`. Choosing `laya`
          starts a decision-model server (Ollaya) in place of Ollama, pulls `laya:en`
          (`--decision-model NAME` for another), and writes `VEYRA_LLM_BACKEND=decision`,
          `VEYRA_DECISION_URL`, `VEYRA_DECISION_MODEL` and `VEYRA_LLM_MODE=live_then_cache`.
          Verified on the laptop through `veyra.ps1`: `up --drafter laya` 19/19 smoke checks,
          the demo's onboarding beat drafts live through Laya; the CPU image drafts too.
Why:      The decision backend existed but could only be switched on by hand.
IDs:      IF-ENV
Files patched: (none in docs/plan beyond this entry); README.md, docs/DEMO_GUIDE.md
TYPE: REQUEST
What:     Shared files changed: two new overlays, `compose/docker-compose.decision.yml` (CPU
          image `ollaya:0.9.0`) and `compose/docker-compose.decision-gpu.yml` (`0.9.0-cuda` plus
          the NVIDIA reservation). Nothing in `docker-compose.yml` or the profiles changes for
          this. Also fixed: `veyra.ps1` read a WSL error message as a distro name.
          Merged with 43ed88b afterwards: `compose/docker-compose.yml` is upstream's, and the
          smoke check keeps upstream's new entries plus the drafter-dependent model server.
ACTION REQUIRED:
  - [ ] @A @B nothing to do unless you object to the two overlays.
  - [ ] @B `./veyra.sh demo` fails at beat 3 on 43ed88b: `demo_engine/auto.py` posts
        `"transport": "http_hec_event"` to `POST /sources`, and control-api answers 422. The
        API takes `http_push` and publishes it as `http_hec_event` (02_CONTRACTS, IF-CONTROL).
        Beats 4 and 5 depend on beat 3, so they fail too.
  - [ ] @C beat 4 of `veyra.sh demo` times out on a stack that holds old events whose raw
        bytes have aged out of Kafka: the backtest waits `VEYRA_EVIDENCE_TIMEOUT_S` (5 s) per
        missing record (control-api `KafkaRawStore`). A fresh stack is not affected.

## 2026-10-02 20:30 — C4 — CLARIFICATION + REQUEST  (contracts v1.5, no bump)
TYPE: CLARIFICATION
What:     The drafter's answers are now limited to fixed lists, and a decision model can draft.
          (1) Ollama's `format` schema is built per request: only this request's token ids, each
          with the fields its value could fill, and enum constants only on enum paths. The
          IF-LLM-DRAFT response shape is unchanged. (2) A second backend, `DecisionClient`, drafts
          through a decision-model server (Ollaya, `POST /api/decide`). It is off by default.
          Measured on the laptop: qwen2.5:3b usable answers 85% → 100%, verify 63% → 81%;
          llama3.2:3b verify 74% → 81% and T3 exact; `laya:en` 87 ms per draft, 100% usable, but
          precision 0.44 / recall 0.33, so Ollama stays the default (reports/C4.md).
Why:      The 2026-09-29 bench failures were answers outside the vocabulary, which a schema can
          forbid; and drafting is multiple choice, which is what decision models are for.
IDs:      IF-LLM-DRAFT (shape unchanged), IF-ENV (additive)
Files patched: reports/C4.md, reports/C4-bench-laptop.md
TYPE: REQUEST
What:     Four additive settings in the shared `veyra_common/settings.py`, all with defaults that
          keep today's behaviour: `VEYRA_LLM_BACKEND` (`ollama` | `decision`, default `ollama`),
          `VEYRA_DECISION_URL`, `VEYRA_DECISION_MODEL`, `VEYRA_DECISION_MIN_PROBABILITY`. No
          profile change. (The `veyra.sh` switch and the compose overlays are the entry above.)
ACTION REQUIRED:
  - [ ] @A @B nothing to do unless you object to the four settings.

## 2026-10-02 — S1 + S2 — CONTRACT-ADDITIVE + CLARIFICATION  (contracts v1.5, no bump)
TYPE: CONTRACT-ADDITIVE
What:     The integration pass before the first full bring-up. `main` could not start at all —
          merge `f79a39b` committed conflict markers into `compose/docker-compose.yml` — and once
          it could, several paths were wired to things that never ran. Additive interface changes:
          `IF-VAULT-INDEX` is now **produced** (the archiver, at each seal); `IF-CONTRACT-YAML`
          envelope layers accept `optional: true`; `IF-API-DEMO` `GET /scenario` carries
          `tamper_query`, and a scenario's `auto[]` steps accept their own `expect:` clauses with
          two new kinds, `contract_active` and `evidence_verifies`; the console's `VaultStatus
          .chain_ok` is now nullable (unknown is not the same as broken).
Why:      Every track reported done while CP1-CP4 were never run, so the gaps were in the seams:
          the demo engine was never started, `demo-auto`'s flows were written from route
          signatures and had four always-fail bugs, the evidence API answered two different
          shapes and the console only understood the fake one, and the reset wiped stores while
          the consumers that flush into them were still running.
IDs:      IF-VAULT-INDEX, IF-CONTRACT-YAML, IF-API-DEMO, IF-API-EVIDENCE, IF-PORTS
Files patched: 06_STATUS_BOARD.md, 04_DEMO_SCRIPT.md (Beat 5 narration now matches the real
          tamper matrix), reports/B2.md, reports/B5.md, docs/DEMO_GUIDE.md, docs/tamper_matrix.md,
          README.md
ACTION REQUIRED:
  - [ ] @A `make cp1` and `make cp2` on the workstation profile, and record the runs.
  - [ ] @B `make llm-cache-seed` (data/llm_cache is empty, so `live_then_cache` has nothing to
        fall back to and CP4 criterion 5 cannot pass), then `make cp4 RUNS=10`.
  - [ ] @C walk every console page against the live stack: the Overview's numbers now come from
        ClickHouse through one adapter, not from the hardcoded fallback.

### What changed, by area

**Blockers.** Resolved the committed merge conflict in `compose/docker-compose.yml`, keeping both
sides (demo-engine *and* the Track B services, the scale replicas and `tools`); the same markers
in this file. Added `b7` to `veyra.sh`'s `SERVICE_PROFILES`, so the demo engine is actually
started and `/api/demo/*` stops answering 502. Gave it `user:` plus the docker group, which it
needs to chmod sealed segments and restart the consumers. `./veyra.sh up` now **fails** when the
smoke check fails instead of printing Ready regardless.

**demo-auto.** Four bugs that could never pass: the drift expectation called `GET /drift` with no
session (401 forever), the analyze reader called `.get` on the `templates` frame which is a list,
the source key was issued while switched to the approver (403), and the drift item was read as
`item["id"]` where `DriftOut` says `drift_id`. Added `test_auto.py`, which drives the flows
against a stub control API.

**Reset.** Stops the stateful consumers *before* wiping, because SIGTERM makes the archiver seal
and the indexer flush — those writes were landing in the freshly wiped vault and index. Clears the
tamper backups where the lab actually puts them (`data/tamper_backup`, not under the vault), and
the edge disk buffers. `make demo-reset` drives the running engine, so the baseline it produces is
paused for the wipe. Restarts in parallel, and the verify pre-warm asks for a real event instead
of searching for the empty string and burning its whole budget.

**Timing.** A beat's `send` actions run concurrently: stage 3's `over_s` values summed to ~115 s
against a 45 s budget, and the burst that fires rule 100111 arrived a minute in. `demo-auto` now
fails a run that starts a step late or overruns `VEYRA_DEMO_AUTO_BUDGET_S`, so AC1 can fail.

**The console wire.** `GET /lineage/overview` and `/lineage/sources` go through one adapter
(`evidence_api.console`) on both paths, so the indexed and degraded answers cannot have different
shapes again — the console was written against the fallback's shape and blanked the Overview the
moment ClickHouse had rows. The fallback's invented numbers (15 EPS, 1500/200/80/20) and the two
fabricated delivery receipts are gone. ClickHouse is probed lazily and retried rather than once at
startup. A contract test compares the adapter's output with the console's own TypeScript
interfaces.

**Evidence.** The archiver publishes `IF-VAULT-INDEX`, so `segments` and `vault_locations` fill and
the vault panel has something true to show. The tamper lab refuses an event whose window is not
signed yet, because tampering it shows nothing and then gets signed in permanently. `04_DEMO_SCRIPT`
Beat 5, `reports/B5.md`, the console mock and the Playwright spec all said `hash_raw` fails under an
insider rewrite; it does not — only `merkle_inclusion` does, and that single red step is the point.

**CEF over syslog.** `envelope: [{syslog: {optional: true}}, {cef: {}}]`, plus a syslog peeler that
no longer eats `CEF:` as an RFC3164 tag. Syslog-framed CEF is tier 1 with byte-accurate offsets
(three new goldens), so the demo sends it over the DMZ listener instead of publishing it straight
onto `raw.acme_ngfw`.

**Verification.** `tools/checkpoints/cp1.py` to `cp4.py` exist and are wired to `make cp1`..`cp4`
(`make e2e-smoke` was a `TODO` echo). The smoke check grew the whole Track B half — lineage rows,
a sealed segment, a signed root — plus a HEC push with a real key and the DMZ listener. `make
typecheck` was green because mypy stopped on a duplicate `conftest` before checking anything; it
now checks 190 files and they pass.

**Safety.** Caddy answers 404 for `/api/control/internal/*`, which was reachable from any browser
tab and included `POST /internal/reset`. A reset no longer signs the presenter out mid-beat. The
control SSE hub filters by tenant. `tools/seed_ch.py` refuses the live database by default — it
writes roots with `immudb_verified = true`, which this build never produces.

## 2026-09-30 17:40 — B6 + B7 — CONTRACT-ADDITIVE + CLARIFICATION  (contracts v1.5, no bump)
TYPE: CONTRACT-ADDITIVE
What:     B6 and B7 are done (B7 with two live ACs deferred). Five additive interface changes and
          three clarifications, none of which break an existing consumer.

          **IF-API-EVIDENCE (additive).**
          (1) `Revision` now carries `field_offsets` (ocsf_path -> [start, end) byte span) and
          `derived_fields` (ocsf_path -> "const"|"vocab:*"|"ts:*"|"enrich"|"base64"). Both are
          lifted out of `ocsf["ulpf"]` by a model validator, so the ClickHouse path and the
          evidence API's vault fallback fill them alike. **This was the bug behind B6's whole
          signature moment:** the engine produced the offsets and `veyra_lineage.rows` indexed
          them, but the read model never exposed them, so the console had been highlighting
          *hardcoded* byte spans and rendering every field as provenance-verified.
          (2) `RawInfo` gains `raw_text` and `raw_b64` — the bytes the contract always said the
          event-detail response carries. The evidence API fills them from the vault.
          (3) `GET /evidence/roots` gains `audit_status` and per-root `signature_ok`,
          `prev_link_ok`, `window_order_ok`, `chain_ok`, from B3's `tools/ledger_audit`. Absent
          flags mean the audit could not run, and the console then shows "unknown", not a tick.
          (4) `GET /lineage/stream` emits a second event type, `event: root`, once per signed
          window. The Evidence page live-appends from it instead of polling.
          (5) A verify step may carry `status: "pending_seal"` when the segment is sealed but its
          window is not signed yet. That is an ordinary wait, not a failure.

          **IF-API-DEMO (additive).** `POST /reset` is now async, returning
          `{started, budget_s}`, with a real `GET /reset/status` carrying per-step `ok`/`ms`
          (it used to be a hardcoded `{"ready": true}`). `POST /untamper` accepts an optional
          `event_uid`. New: `GET /tamper/active`, `GET /baseline`,
          `POST /baseline/{pause,resume}`, `POST /hotkeys` (the console reports its registered
          combos so preflight can check them). `GET /scenario` also returns each stage's
          `expects` labels and stage 2's resolved onboarding `samples`.

          **CLARIFICATION — immudb anchoring is not implemented, and nothing pretends it is.**
          `immudb_verified` reports `status: not_implemented`; the console renders it as a
          neutral grey node, never red and never green. B6's AC2 therefore reads **7 green + 1
          grey**, not 8 green. Animating it green would break the phase file's own honesty rule.

          **CLARIFICATION — the demo scenario lives at `demo/scenarios/sih_main.yaml`**, selected
          by `VEYRA_DEMO_SCENARIO`. The previous `demo/scenario.yaml` is gone. It had also lost
          `seed` and the baseline's `exclude_patterns: ["Failed password"]`; without that
          exclusion the sshd brute force flows during the baseline, Wazuh fires rule 100111
          before Beat 3, and "Wazuh now catches the attack it missed" is untrue by the time the
          demo gets there.

          **CLARIFICATION — a verify/preflight/expectation that cannot be evaluated FAILS.**
          Three places had been reporting success unconditionally: preflight returned fixed
          `PASS` strings (`"< 50 ms host/container skew"` was a literal) and turned exceptions
          into PASS; `_evaluate_expectation` returned `True` for any clause it did not recognise,
          so stage 3's Wazuh 100111 expectation had never once been evaluated; and every reset
          step was wrapped in a swallow-all `except` with `ok` hardcoded true. All three now fail
          loudly, which is the only way those commands are worth running.

          New settings, all with working defaults: `VEYRA_DEMO_SCENARIO` (`sih_main`),
          `VEYRA_DEMO_RESET_BUDGET_S` (90), `VEYRA_DEMO_EDGE_DMZ_HOST`,
          `VEYRA_DEMO_EDGE_CORE_HOST`, `VEYRA_DEMO_GATEWAY_URL`, `VEYRA_DEMO_ENGINE_PORT`,
          `VEYRA_WAZUH_INDEXER_URL`, `VEYRA_WAZUH_MANAGER_URL`, `VEYRA_WAZUH_DASHBOARD_URL`,
          `VEYRA_WAZUH_API_USER`, `VEYRA_IMMUDB_HOST`.

          **DEVIATION.** `compose`'s new `demo-engine` service (profile `b7`, port 8300) mounts
          `/var/run/docker.sock`, demo profile only, because the reset restarts the stateful
          consumers through the Docker Engine API. It talks to the API over the socket with
          `httpx` rather than the Docker SDK, because this repository's own top-level `docker/`
          directory shadows the SDK's import name depending on the working directory.
Why:      B6 and B7 are the two phases the demo's last two beats and its reproducibility rest on.
          Most of this entry is not new capability but the difference between a page that looks
          right and one that is right.
IDs:      IF-API-EVIDENCE (additive), IF-API-DEMO (additive), IF-ULPF (consumed),
          IF-TOPICS (now honoured by the reset rather than hardcoded)
Files patched: 02_CONTRACTS.md (IF-API-EVIDENCE, IF-API-DEMO), 03_INFRA_PROFILES.md §2.4
          (new demo settings), profiles/*.env, 00_MASTER.md §7 (docker socket deviation),
          06_STATUS_BOARD.md (B1-B7 rows + report links), track-B-evidence/B6_*.md and B7_*.md,
          reports/B1.md-B7.md (B1-B5 are the backfill A5 asked for).
ACTION REQUIRED:
  - [ ] @C `EventRevision` now carries `field_offsets` and `derived_fields`, and `RawInfo`
        carries `raw_text`/`raw_b64`. C2's replay panel and C6's drift review can highlight raw
        bytes exactly as the Lineage page does — reuse `console/src/pages/lineage/fields.ts`
        (`buildFields`) rather than writing a second provenance rule.
  - [ ] @C `api.text()` now exists in `console/src/api/client.ts`. `api.get()` always calls
        `response.json()`, which is why `/evidence/pubkey` (text/plain PEM) had been rejecting
        every time. Use `api.text()` for any other text endpoint.
  - [ ] @C `make demo-auto` drives your real endpoints as a human would (login → analyze SSE →
        draft → submit → demo-switch → approve → promote → replay). If a response key differs
        from what `services/demo_engine/src/demo_engine/auto.py` reads, the run names the exact
        failing call — the cheapest contract test the control plane has.
  - [ ] @A The console now trusts `ulpf.field_offsets` completely: a field whose span does not
        slice out its value renders a red provenance warning on screen. That is intended, and it
        makes an engine regression visible immediately rather than at CP4.
  - [ ] @all At the rehearsal, close B7 AC1 (`make demo-reset` under 90 s, post-reset state per
        04_DEMO_SCRIPT §2), B7 AC2 (`make demo-auto N=10`) and B6 AC2's cold <2 s verify. Also
        confirm the reset's Kafka ordering choice: `control` is **excluded** from the delete set
        because step 2 has already republished it.
  - [ ] @B `make typecheck` is broken repo-wide on a **pre-existing** duplicate `conftest`
        module (`services/ingest_gateway/tests` vs `services/control_api/tests`), and
        `tools/tamper.py` resolves under two module names. Neither is B6/B7 code; mypy is clean
        on the files this work touched. Worth fixing before CI is trusted.

## 2026-09-30 — S2 — DECISION + REQUEST @A @B  (contracts v1.5, no bump)
TYPE: DECISION
What:     One-command setup for reviewers and the workstation demo: `./veyra.sh` (Linux/WSL/macOS)
          and `.\veyra.ps1` (Windows; runs veyra.sh through WSL or a toolbox container). It
          writes `.env.runtime`, builds the image and the console (Node 25 in a container), starts
          everything, sets up Wazuh, pulls the AI model into an Ollama container (GPU when
          visible), smoke-checks (`tools/veyra_check.py`), and adds `demo` (a narrated
          walkthrough, `tools/demo/walkthrough.py`) and `load` (Kafka perf test + a pipeline
          load generator, `tools/bench/load_raw.py`). New README.md and docs/DEMO_GUIDE.md.
          Shared-file changes this needed:
          - `compose/docker-compose.yml`: services for **lineage-indexer (b1), archiver (b2),
            integrity (b3), evidence-api (b4)**, all as `VEYRA_UID` (they share the 0400 keys);
            `drift-worker` now runs as `VEYRA_UID` too (it writes `data/state`);
            `normalizer-2..6` (profile `scale`, distinct `VEYRA_INSTANCE`) for load tests;
            a `tools` one-off service.
          - New overlays: `docker-compose.ollama.yml`, `.gpu.yml`, `.ui.yml` (Kafka UI on 8085),
            `.fastdata.yml` (Kafka/ClickHouse data in volumes when the repo is on a Windows drive).
          - `Caddyfile`: `/api/evidence/*` now strips only `/api`, because evidence-api's routes
            start with `/evidence/` (every call through Caddy was a 404 before).
          - `.gitattributes`: LF for scripts and container configs. With core.autocrlf=true,
            `wazuh/entrypoint-veyra.sh` was being checked out as CRLF and bash fails on it.
          - `.dockerignore` (new); `Makefile`: `data/control` in `up`, `veyra-*` passthroughs.
Why:      Tomorrow's workstation demo (Windows + Docker Desktop) and reviewers must be able to run
          the system with one command and only Docker + git installed.
IDs:      IF-PORTS (8085 Kafka UI, 11434 now also a container), IF-TOPICS (unchanged)
Files patched: README.md, docs/DEMO_GUIDE.md, compose/*, Caddyfile, Makefile, .gitattributes.
ACTION REQUIRED:
  - [ ] @B Your B1–B4 services are now in compose (profiles b1–b4) and start with `./veyra.sh up`.
        Please check the entries, and note the Caddy evidence path fix; the console's live
        mode still needs your `/lineage/*` routes.
  - [ ] @A `normalizer-2..6` rely on `VEYRA_INSTANCE` for distinct transactional ids; please
        confirm that is the intended knob before A6's scaling bench uses it.

## 2026-09-29 23:30 — C4 — CLARIFICATION  (contracts v1.5, no bump)
TYPE: CLARIFICATION
What:     C4's GPU items were measured on this laptop's RTX 4050 (6 GiB) with Ollama 0.34.4 on the host.
          AC2 and AC5 pass: live p95 is 5.3 s (qwen2.5:3b) and 6.3 s (llama3.2:3b), and
          `live_then_cache` falls back at 25 047 ms (bound 25 200). Accuracy is the real limit: both
          models map T3's relay IP to `src_endpoint.port`, and verify flags it, so **live drafting is not
          safe for Beat 4**, and `make llm-cache-seed` would record that wrong draft. It was not run.
          `cache` mode with an empty cache falls back to the heuristic, which gets T3 right.
          Three drafter fixes, none changing an interface: the timeout now covers a whole draft,
          retry included (before, it applied per call, so a draft could take 50 s); `warm()` has its own
          180 s budget (the first load after boot is 26–32 s, and Ollama aborts a load when the client
          disconnects); the bench reports each model's own VRAM and calls its verify column "Verify %".
Why:      AC2 and AC5 were waiting for a GPU (TC40).
IDs:      IF-LLM-DRAFT (unchanged)
Files patched: track-C-control-console/C4_llm_drafter.md, reports/C4.md, reports/C4-bench-laptop.md,
          06_STATUS_BOARD.md.
ACTION REQUIRED:
  - [ ] @C Owner decision: keep `VEYRA_LLM_MODEL=qwen2.5:3b` (more valid drafts: 85% vs 56%) or switch
        to llama3.2:3b (better precision: 0.59 vs 0.36). Neither drafts T3 correctly.
  - [ ] @C Owner decision: how Beat 4's cached draft gets made. Options: seed from a reviewed draft (the
        heuristic's T3 draft is correct), or improve the prompt and re-bench before running
        `llm-cache-seed`.
  - [ ] @B B7's `demo-reset`/preflight should call `make llm-warm` if the demo ever uses `live*`: the
        first load after boot is longer than `LLM_TIMEOUT_S`.

## 2026-09-29 16:30 — A5 — CLARIFICATION + REQUEST @B  (contracts v1.5, no bump)
TYPE: CLARIFICATION
What:     IF-SHADOW is **produced** now. The record shape is unchanged (B1's `shadow_row` already
          indexed it), so this is not a bump — but three things are worth writing down:
          (1) a `shadow` record is produced **in the same Kafka transaction** as the event it
          describes, so a shadow row can never outlive the event it is about;
          (2) `changed_fields` compares **values**, not offsets. A field that stops being located but
          keeps its value is not a change, and `regressions` lists paths the active version claimed
          and the candidate no longer does (plus a tier that got worse) — that is the list an approver
          must read before promoting;
          (3) `changed_fields`/`regressions` are OCSF paths, and `observables` appear by name
          (`observables.ip_1`), the same convention A4's `provenance_check` uses.
          Two fixes that are not interface changes but change behaviour:
          - **`replay.raw` never worked before A5.** `Envelope` is `extra="forbid"` and a replayed
            record carries an extra `replay` block, so every replay message was rejected as a broken
            envelope and DLQ'd as `schema_invalid`. The normalizer now validates `ReplayEnvelope` when
            the value has a `replay` key. C2's replay jobs would have timed out at CP3 otherwise, with
            a reason pointing at the wrong track.
          - **`Budget.expired()` read a limit of 0 as "already over"**, so a backtest asking for "no
            limit" turned every event into `budget_exceeded`. 0 now means no limit.
          New knob `VEYRA_SHADOW_BUDGET_US` (default = `ENGINE_BUDGET_US`): the canary's own budget.
          Over it, the comparison is skipped (`veyra_shadow_skipped_total`), never the event.
          New metrics: `veyra_shadow_events_total{contract}`, `veyra_shadow_regressions_total`,
          `veyra_shadow_skipped_total{reason}`, `veyra_replay_events_total{job}`.
Why:      A5 makes the contract loop's last step real: a canary that cannot affect output, and replayed
          history that supersedes itself instead of duplicating.
IDs:      IF-SHADOW (produced; shape unchanged), IF-ENVELOPE (replay block now actually honoured),
          IF-ENGINE-LIB (`backtest` gains an optional `budget_us`; `Engine.shadow` is new)
Files patched: 02_CONTRACTS.md (none needed), 03_INFRA_PROFILES.md §2.3 (`SHADOW_BUDGET_US`),
          profiles/*.env, track-A-dataplane/A5_shadow_replay_revisions.md, reports/A5.md,
          06_STATUS_BOARD.md.
ACTION REQUIRED:
  - [ ] @B B1's `shadow_diffs` table now receives real rows — worth a look at CP2, because until today
        that code path had never seen a message.
  - [ ] @B **Plan-state debt:** B1–B5 have code on `main` (archiver, integrity, evidence-api, tamper
        lab) with no reports, no status-board rows and phase files still `todo v1.0`. A is not writing
        those for you; the board currently understates what exists, which will bite at CP2 when
        someone has to know what is real.
  - [ ] @B `compose/docker-compose.yml` (your file): `normalizer` needs
        `user: "${VEYRA_UID:-1000}:${VEYRA_GID:-1000}"`, like `control-api` and `ingest-gateway`.
        Without it the container (uid 10001) cannot write `data/state/`, so A4's crash-loop journal is
        silently inert — it degrades by design, so nothing looked broken. A has made the change; any
        other service that writes under `data/` needs the same.
  - [ ] @C `backtest()` gained `field_coverage: {ocsf_path: pct}` (share of events in which the
        candidate produced that path). Additive — `run_backtest`'s `asdict` already passes it through,
        so it is in the API response now; `console/src/api/types.ts::BacktestResult` needs one optional
        field if C2's panel wants to show it.
  - [ ] @C Replay progress works end to end now: the normalizer sets `lineage.replay_job_id` and
        `revision` from the block **exactly as sent**, so `ReplayWatcher` counts what it expects. A
        replay whose block is malformed is delivered as an ordinary event rather than dropped, which
        means a job with a bad block reports `timed_out`, not `failed`.

## 2026-09-29 — C6 — CONTRACT-ADDITIVE  (contracts v1.4 → v1.5)
TYPE: CONTRACT-ADDITIVE
What:     IF-API-CONTROL gains `POST /onboarding/use-library {source_id, pack}` → 201 contract version
          (canary): a matched library pack cloned as the source's version 1 (TC41). Also, documented:
          `GET /routes` → `{routes}` is built; `POST /drift/{id}/draft` accepts a `drafting` item and the
          new draft supersedes the old one (a superseded draft no longer changes the item);
          `GET /drift?state=&source_id=` filters on the exact state; approve answers a role 403 before
          the four-eyes 403.
Why:      C6's onboarding offers a matched library pack instead of drafting, and the demo's 5 s cache
          fallback re-drafts an item that is still drafting.
IDs:      IF-API-CONTROL
Files patched: 02_CONTRACTS.md (IF-API-CONTROL + header v1.5), every plan file's `contracts:` header,
          track-C-control-console/C6 (implementation notes), reports/C6.md, 06_STATUS_BOARD.md.
ACTION REQUIRED:
  - [ ] @A @B Nothing to change: the header bump is additive (a new control-api endpoint).

## 2026-09-29 — C6 — REQUEST  (contracts v1.5)
TYPE: REQUEST
What:     1. Delivery shows route definitions (`GET /api/control/routes`) and live statistics
          (`/lineage/overview` routes). Its "recent receipts" table needs `GET /lineage/receipts?limit=`
          → `[{route_id, event_uid, status, at, detail}]` (TC44); until then the page says so.
          2. After a replay and from each backtest, the console links to `/lineage?q=<template_sig>`
          (TC45): a replay job doesn't know its event uids, so the search is by template sig.
Why:      IF-API-EVIDENCE has no receipts listing, and B6's search parameters aren't fixed yet.
IDs:      IF-API-EVIDENCE
Files patched: none (B owns IF-API-EVIDENCE and B6).
ACTION REQUIRED:
  - [ ] @B (B4) Add `GET /lineage/receipts?limit=` or tell C to drop the receipts table.
  - [ ] @B (B6) Make the Lineage search accept `q=<template_sig>`, or tell C the parameter to use.

## 2026-09-29 — C5 — CLARIFICATION  (contracts v1.4, no bump)
TYPE: CLARIFICATION
What:     PR #6 (`c5-console-shell`) is on `main`. C5 stays in-progress: the live halves of AC1 and AC2
          wait for B1/B4's evidence-api (CP2). C6 starts on `c6-console-pages`.
Why:      The status board and reports/C5.md still described C5 as unmerged.
IDs:      none
Files patched: 06_STATUS_BOARD.md, reports/C5.md.
ACTION REQUIRED:
  - (none)

## 2026-09-29 — C4 — CLARIFICATION  (contracts v1.4, no bump)
TYPE: CLARIFICATION
What:     PR #5 (`c4-drafter`) is on `main`. C4 stays in-progress: the live p95 bench and the
          two-model report (AC2, AC5) wait for a GPU (TC40).
Why:      The status board and reports/C4.md still described C4 as unmerged.
IDs:      none
Files patched: 06_STATUS_BOARD.md, reports/C4.md.
ACTION REQUIRED:
  - (none)

## 2026-09-29 — C5 — VERSION-PIN  (contracts v1.4)
TYPE: VERSION-PIN
What:     Console toolchain (C5), exact versions in `console/package-lock.json`: vite 7.3.6, react 19.3.0,
          react-router 7.18.4, @tanstack/react-query 5.104.0, tailwindcss 4.3.3, vitest 4.1.11,
          jsdom 27.4.0, typescript 5.9.3, msw 2.15.0, @playwright/test 1.63.0, lucide-react 1.48.0.
          Node 25 (D17) for the build only; npm 11 (npm 10.8 crashes on jsdom's optional `canvas` peer).
Why:      These majors are the newest whose declared `engines` accept Node 25 (plan TC12). Vite 8,
          react-router 8, vitest 5 and jsdom 30 exclude odd Node majors or need Node ≥ 22.22. The
          IF-VERSIONS row was reserved for C5, so filling it is not a contract bump.
IDs:      IF-VERSIONS
Files patched: 02_CONTRACTS.md (IF-VERSIONS React/Vite/Tailwind row), reports/C5.md,
          track-C-control-console/C5 (implementation notes), C6 (downstream note), 06_STATUS_BOARD.md.
ACTION REQUIRED:
  - (none; closes the S0 follow-ups "@C Pin React/Vite/Tailwind (C5)" and "@C set engines in C5")

## 2026-09-29 — C5 — REQUEST  (contracts v1.4)
TYPE: REQUEST
What:     The console reads IF-API-EVIDENCE's overview and source-health endpoints in these exact shapes
          (`console/src/api/types.ts`; worked examples in `console/src/mocks/fixtures.ts`):
          - `GET /lineage/overview?tenant=` → `{eps_1m, totals_by_tier: {"1".."4": n},
            sources: [{source_id, zone, eps, tiers, last_seen|null}],
            routes: [{route_id, delivered_per_min, failed_per_min, lag_s|null, breaker: closed|open|half_open|null}],
            vault: {segments, last_sealed_at|null, last_root: {window_id, window_end, immudb_verified}|null, chain_ok},
            as_of, tier_history?: [{"1".."4": n}, …]}`. `tier_history` is optional: per-second tier
            counts for the last 15 minutes, oldest first, ending at `as_of`. Send it with the HTTP
            response so the tier bar opens full; SSE ticks may omit it.
          - `GET /lineage/sources?tenant=` → `[{source_id, tenant_id, zone, transport, contract_ref|null,
            expected_eps, actual_eps, last_seen|null, tiers, clock_skew_p50_ms|null}]`
          - `GET /lineage/stream` (SSE): `event: overview`, data = the whole overview object above, about
            once a second. Omitting `tenant` means all tenants (platform users).
          - Tenant scope comes from the session, not the query: a user pinned to a tenant sees only
            that tenant whatever `?tenant=` says; only platform users (`tenant: "*"`) may choose.
Why:      IF-API-EVIDENCE lists these fields loosely; C5 had to pick exact names to build against mocks
          (plan TC14). Agreeing now avoids a rename at CP2. B may choose other names: tell C, and C
          changes the types and fixtures in one place.
IDs:      IF-API-EVIDENCE
Files patched: none (B owns IF-API-EVIDENCE).
ACTION REQUIRED:
  - [ ] @B (B1/B4) Confirm or amend these shapes in IF-API-EVIDENCE, including `tier_history` on
        the HTTP overview (without it the Overview tier bar starts empty) and session-enforced
        tenant scope.
  - [ ] @B (B6) The frozen component APIs are in reports/C5.md ("Notes for downstream phases");
        the lineage and evidence routes are placeholders in `console/src/shell/AppShell.tsx`.

## 2026-09-29 01:10 — C4 — CLARIFICATION  (contracts v1.4, no bump)
TYPE: CLARIFICATION
What:     S1 CP4 check 5 reads `LLM_MODE=live_then_cache` with Ollama stopped → the cached draft
          is used and the demo still passes. `live` alone does not fall back to the cache.
Why:      Plan 5's mode table: only `live_then_cache` consults the cache when the model is down.
IDs:      IF-LLM-DRAFT
Files patched: shared/S1_integration_checkpoints.md.
ACTION REQUIRED:
  - [ ] @A @B Nothing to do.

## 2026-09-29 01:10 — C4 — DECISION  (contracts v1.4, no bump)
TYPE: DECISION
What:     TC32–TC40, as executed on `c4-drafter`. Onboarding takes an existing `source_id`; the
          contract id drops `src_` and a trailing `_NN` (`src_authsrv_01` → `authsrv`), and that
          id is the template-sig scope. Layer detection uses A4's detectors and still returns
          contract-envelope layers. Auto-draft creates a draft and does not submit. TC40: the
          laptop stays on `VEYRA_LLM_MODE=cache` until a GPU works. The live p95 bench and the
          two-model report (AC2, AC5) are deferred. `make llm-warm`, `make bench-llm` and
          `make llm-cache-seed` are in the Makefile; they call Ollama and were not run here.
Why:      Those decisions were locked before coding. The bench measurement needs a GPU this
          laptop does not have.
IDs:      IF-LLM-DRAFT, IF-API-CONTROL
Files patched: 06_STATUS_BOARD.md, track-C-control-console/C4_llm_drafter.md, reports/C4.md,
          profiles/laptop.env (mode set when the drafter landed).
ACTION REQUIRED:
  - [x] @C Run `make bench-llm MODELS=qwen2.5:3b,llama3.2:3b` when a GPU is available, and set
        `VEYRA_LLM_MODEL` from the winner.  <!-- C: bench run 2026-09-29 (reports/C4-bench-laptop.md); no clear winner, so the model choice is an owner decision in the 23:30 C4 entry -->

## 2026-09-29 01:10 — C4 — CONTRACT-ADDITIVE  (contracts v1.4, no bump)
TYPE: CONTRACT-ADDITIVE
What:     IF-API-CONTROL's draft and onboarding lines now match what control-api serves.
          `POST /drift/{id}/draft {mode?}` → 202 `{draft_id}` (the author is whoever later
          submits). `PATCH /drafts/{id}` takes `{template_sig?, class?, activity?, mappings}`
          and returns 422 when a mapping leaves the closed vocabulary. `POST /onboarding/analyze`
          takes `{source_id, samples, mode?}` and streams `classification`, `templates`,
          `library`, `draft`, `done`, `error`. The SSE `draft` payload is
          `{draft_id, drift_id, state, source_id}`.
Why:      The section already named the routes. The bodies were the Plan 5 shape (TC32), not the
          earlier one-shot JSON sketch. No new field on an event envelope, so the contract
          version stays v1.4.
IDs:      IF-API-CONTROL
Files patched: 02_CONTRACTS.md (IF-API-CONTROL).
ACTION REQUIRED:
  - [x] @C C5/C6 should call these bodies, not the old `{tenant_id, source_name, transport}` analyze sketch.  <!-- C5: calls none of these routes; C6 2026-09-29: /onboard sends {source_id, samples:[text], mode?} -->

## 2026-09-28 23:55 — C2/C3 — CLARIFICATION + DECISION  (contracts v1.4, no bump)
TYPE: CLARIFICATION
What:     PR #3 (`c2-c3-registry-drift`) is on `main`. C2 and C3 stay in-progress: C2 AC2/AC4
          live halves still need A5 and B; C3 AC1's under-10s smoke has not run. TC40 (Plan 5):
          the laptop uses `VEYRA_LLM_MODE=cache` until Ollama runs on a GPU. The live two-model
          bench stays deferred. C4 is not started.
Why:      The status board and the Track C roadmap still described C2/C3 as unmerged, and S0's
          GPU-vs-cache request to @C was still open.
IDs:      none
Files patched: 06_STATUS_BOARD.md, track-C-control-console/{C1,C2,C3}, reports/{C2,C3}.md.
          The Track C roadmap under docs/superpowers/ is gitexcluded; it was updated locally only.
ACTION REQUIRED:
  - [x] @C Set `VEYRA_LLM_MODE=cache` in `profiles/laptop.env` when C4 lands. (`profiles/laptop.env` is `cache`.)
  - [ ] @C C3 live timing smoke (`make up PROFILE=laptop SERVICES="a3 c1 c3"`) is still open.

## 2026-09-28 21:00 — A2 — CONTRACT-ADDITIVE  (contracts v1.3 → v1.4)
TYPE: CONTRACT-ADDITIVE
What:     IF-ENVELOPE gains one optional field, `hec_meta`, set only by the gateway's HEC event
          endpoint: `{"time": float, "host": str, "source": str, "sourcetype": str, "index": str}`,
          each optional, `null` for every other ingestion path. It holds what a pushing client
          *claimed* about its event. It is deliberately **not** merged into the envelope proper:
          `hec_meta.time` is neither `received_time` (when VEYRA read the bytes) nor the event time
          (which the engine derives from the bytes; `ulpf.time.source` says which). The normalizer
          surfaces the block under `unmapped.hec_meta` and maps nothing from it.
Why:      A shipper's own timestamp and sourcetype are evidence about the sender and are useful when
          an event is unparseable — but promoting a client-supplied time into the event time would
          let a misconfigured or hostile sender rewrite history, so the two are kept apart.
IDs:      IF-ENVELOPE (additive), IF-NORM-EVENT (`unmapped` content only; the schema is unchanged)
Files patched: 02_CONTRACTS.md (IF-ENVELOPE example + rule, version header v1.4), every plan file's
          `contracts:` header (25 files), packages/veyra_common/fixtures/envelope.json,
          track-A-dataplane/A2_ingest_gateway.md, reports/A2.md, 06_STATUS_BOARD.md.
ACTION REQUIRED:
  - [ ] @B B1's `raw_events` builder can take `hec_meta` as columns (or one JSON column) if the
        console wants to show "what the shipper claimed"; ignoring it is also fine — the field is
        optional and every syslog envelope has it `null`. Nothing breaks either way, because
        `veyra_common.models.Envelope` parses old and new envelopes identically.
  - [ ] @C C1's key card already matches what the gateway serves (`keys.py` hard-codes port 8088 and
        the `Splunk` scheme, and `/v1/batch` is the batch URL) — no change needed, but
        `services/ingest_gateway/API.md` is now the copy to embed rather than a hand-written snippet.

## 2026-09-28 21:00 — A2 — VERSION-PIN  (contracts v1.4)
TYPE: VERSION-PIN
What:     `python-multipart>=0.0.20` added to `services/ingest_gateway`. FastAPI needs it for
          multipart form parsing, which is what `POST /v1/batch` takes (a file upload). It is the
          only new runtime dependency A2 introduces.
Why:      IF-VERSIONS says every pin is logged. Batch upload is an A2 acceptance criterion, and a
          multipart endpoint without it fails at import time, not at request time.
IDs:      IF-VERSIONS
Files patched: services/ingest_gateway/pyproject.toml, uv.lock.
ACTION REQUIRED:
  - [ ] @B @C Nothing to do; `uv sync` picks it up.

## 2026-09-28 21:00 — A2 — CLARIFICATION  (contracts v1.4)
TYPE: CLARIFICATION
What:     Two small shared-code changes A2 needed, both behaviour-preserving for existing services:
          (1) the `control`-topic follower moved to `veyra_common.control.ControlReader` — the loop,
          the high-watermark readiness rule and the tombstone handling now live in one place, with
          `normalizer/control.py` keeping only its engine-specific state. A3's control tests pass
          unmodified, which is the evidence it was a move and not a rewrite. The gateway is the third
          consumer of `control`; the router (A6) will be the fourth and should use the same class.
          (2) `compose/docker-compose.yml`'s `ingest-gateway` now runs as
          `${VEYRA_UID}:${VEYRA_GID}`, like control-api and immudb. It has to: control-api writes
          `data/keys/api_pepper` mode 0400 as the invoking user, and without the pepper the gateway
          cannot authenticate anybody.
Why:      Three copies of the readiness rule would be three chances to reintroduce the "ready with an
          empty state" bug; and the pepper permission problem is invisible until the first request,
          where it looks like a bad key rather than a deployment fault.
IDs:      none (internal structure and compose)
Files patched: packages/veyra_common/src/veyra_common/control.py (new),
          services/normalizer/src/normalizer/control.py, compose/docker-compose.yml.
ACTION REQUIRED:
  - [ ] @B The compose line above is in your file — review it. Any service that reads
        `data/keys/*` needs the same treatment.
  - [ ] @C When C1 issues a key it must be reachable by the gateway within seconds: the gateway
        follows `control` and needs no restart, which C1's revoke path already satisfies (it
        republishes the key with `status="revoked"` rather than tombstoning it — please keep that,
        A2's AC2 test depends on it).
## 2026-09-28 — C1/C2/C3 — CONTRACT-ADDITIVE  (contracts v1.3 → v1.4)
TYPE: CONTRACT-ADDITIVE
What:     IF-API-CONTROL (all additive):
          - new endpoints: `POST /auth/demo-switch` (demo mode), `GET /sources/{id}/keys`,
            `POST /contracts/{id}/versions/{v}/backtest`, `GET /replay?contract_id=`, `GET /drift/{id}`,
            `POST /drift/{id}/dismiss`;
          - `/internal/drift` gains `related_sigs` and `sample_event_uids`;
          - the SSE `contract`, `replay` and `drift` payloads are documented.
          IF-API-DEMO: drift-worker `POST /reset` (control-api's `/internal/reset` calls it).
          IF-TOPICS: control-api reads `raw.*` by `raw_ref` and `lineage` by `replay_job_id`, both
          assign-only.
          IF-CONTROL, details written down: pepper hashing and `pepper_id`; transport vocabulary; a
          contract is published only once it has an active version; reset tombstones stale keys.
Why:      C1 is merged, and C2 + C3 are code-complete on branch `c2-c3-registry-drift`. Reports in
          reports/C1.md, C2.md and C3.md.
IDs:      IF-API-CONTROL, IF-API-DEMO, IF-TOPICS, IF-CONTROL
Files patched: 02_CONTRACTS.md (v1.4), every plan file's `contracts:` header,
          track-C-control-console/{C1,C2,C3} (implementation notes), {C4,C5,C6} (downstream notes),
          06_STATUS_BOARD.md, reports/C1.md, reports/C2.md, reports/C3.md.
ACTION REQUIRED:
  - [x] @A A2: hash API-key secrets exactly as IF-CONTROL now states,
        `sha256(pepper_bytes + secret_utf8)`, and match on `pepper_id`.
        <!-- done in A2: `KeyRegistry.digest` is that formula (a test asserts it equals
             control_api.keys.secret_digest's), and a key whose `pepper_id` is not this gateway's
             `"p_" + sha256(pepper)[:8]` is refused with that stated reason, so a rotated pepper reads
             as a pepper problem instead of "every key is wrong". -->
  - [ ] @A A5: set `replay_job_id` on `lineage` records for `replay.raw` input. control-api's replay
        progress counts exactly those records; without them every replay job times out.
  - [ ] @A A3: `tools/mock_control_publish.py` can go; control-api now publishes `control`.
        <!-- A2 kept it for now: it publishes an API key with a chosen quota and can revoke one, which
             is how A tests the gateway without bringing up C1's HTTP surface. Delete it at CP3, once
             the demo runs entirely through control-api. -->
  - [ ] @B B4: serve `/templates/{sig}/events` (B's `TemplateEvent` rows, including `raw_ref` and the
        latest `revision`) and `/events/{uid}` at the paths Caddy forwards (`/api/lineage` stripped).
        control-api's backtest and replay call exactly those.
  - [ ] @B B4 (later, not blocking): an event listing by source and tier, so C2's backtest can
        sample tier-1 events for regressions.
  - [ ] @B Caddyfile (adopted from S0): add `respond /api/control/internal/* 404`. control-api's
        internal endpoints are currently reachable from the browser.
  - [ ] @B B7: no change. One `POST /internal/reset` now also resets the drift worker.

## 2026-09-28 — C1/C3 — VERSION-PIN  (contracts v1.4)
TYPE: VERSION-PIN
What:     Control plane: sqlmodel 0.0.47, argon2-cffi 25.1.0, dulwich 1.2.15 (pure-Python git; the
          image has no git binary) and httpx 0.28.1 in control-api. Drift: drain3 0.9.11, which pulls
          jsonpickle 1.5.1 and cachetools 4.2.1 (old but working on 3.12; watch for conflicts).
Why:      S0 left Drain3 to C3; the control-plane libraries arrived with C1 and were never recorded.
IDs:      IF-VERSIONS
Files patched: 02_CONTRACTS.md (IF-VERSIONS), uv.lock.
ACTION REQUIRED:
  - [x] @C Pin React/Vite/Tailwind when C5 lands in `console/` (Drain3 is now done).  <!-- C: 2026-09-29, C5 VERSION-PIN -->

## 2026-09-28 — C2/C3 — DECISION  (contracts v1.4)
TYPE: DECISION
What:     Decisions in C2 and C3 (details in reports/C2.md, C3.md):
          - Four-eyes: the author is whoever *submits* a version. Approving your own version is 403
            with a message containing "four-eyes". Promote needs an approved canary. Rollback restores
            only a version that was active before.
          - A brand-new contract's first version is not on `control` while it is a canary; it
            appears when promoted.
          - Drift items resolve when the **active** version covers them, not the canary.
          - Drift worker restart: counts are persisted beside the Drain3 state.
          - Library packs live in the registry's `library/` (tenant `t_library`). Library
            `linux_sshd` covers all 24 corpus shapes. This answers A3's REQUEST @C below. The seeded
            `t_ntro_core/linux_sshd@1` is unchanged, so A's snapshots stay valid.
Why:      The phase files left these open or ambiguous.
IDs:      IF-CONTROL, IF-API-CONTROL
Files patched: reports/C2.md, reports/C3.md, track-C-control-console/C2, C3.
ACTION REQUIRED:
  - [ ] @C Owner decision: should the demo's seeded `linux_sshd` become the library version?
        Otherwise 13 sshd shapes sit in the demo's drift inbox beside authsrv. If yes: REQUEST @A to
        regenerate `packages/veyra_engine/tests/expected/linux_sshd.log.json`.
  - [ ] @C Owner decision before C4's bench (S0 REQUEST below): GPU, or `VEYRA_LLM_MODE=cache` on the
        laptop.
  - [ ] @C Move the registry's `seed` tag to the commit with the golden samples and library packs,
        and force-push it. Otherwise a demo reset deletes them.

## 2026-09-28 18:00 — A4 — CLARIFICATION + REQUEST @B  (contracts v1.3, no bump)
TYPE: REQUEST
What:     `Settings.ch_url` (default `""`) added to `veyra_common.settings`, because
          `veyra_lineage.client.ch_url` (B) already reads it — its own docstring says
          "`VEYRA_CH_URL` wins over `VEYRA_CLICKHOUSE_URL`" — and the field did not exist, so all 5
          B1 integration tests errored with `'IndexerSettings' object has no attribute 'ch_url'`.
          A owns `veyra_common`, so A added the field. Empty means "not overridden".
          Two more knobs are still missing, and they are in **B's** file:
          `services/lineage_indexer/src/lineage_indexer/settings.py` has no `index_batch_rows`
          or `index_batch_ms`, while `tests/int/test_lineage_index.py::_cfg` passes both (pydantic
          silently ignores them, so the indexer then reads attributes that are not there). A did not
          guess the intended batching semantics.
Why:      Found while running the A4 gate (`make test-int`). Cross-track seam: the A-owned half is
          fixed, the B-owned half needs B.
IDs:      none (settings knobs; IF-CH-SCHEMA unaffected)
Files patched: packages/veyra_common/src/veyra_common/settings.py, 05_CHANGELOG.md.
ACTION REQUIRED:
  - [ ] @B Add `index_batch_rows` and `index_batch_ms` to `IndexerSettings` (or change the indexer
        and the test to use the existing `norm_batch_max`/`norm_batch_ms`), then re-run
        `uv run pytest tests/int/test_lineage_index.py -m int`. 3 of the 5 still fail on this;
        the other 2 pass now.
  - [ ] @B Also add the knobs to `03_INFRA_PROFILES.md` §2 and `profiles/*.env`, and close out B1
        (phase file status, report, status board row, `lineage-indexer` in compose).

## 2026-09-28 17:30 — A4 — CLARIFICATION  (contracts v1.3, no bump)
TYPE: CLARIFICATION
What:     Tier 3 is real. An unregistered or unknown-template event now arrives as tier 3 with
          observables, `unmapped`, a class hint and **byte offsets**, instead of falling out as
          tier 4. IF-ULPF already specified all of this, so nothing in the interface changed — but
          two things are worth writing down for consumers:
          (1) `ulpf.derived_fields` **values** are engine vocabulary, not interface. A4 emits
          `vocab:severity_words`, `ts:token`, `received`, `from:<path>`, `token:no-span`,
          `text:no-span`, `default:unknown`, `default:informational`, `default:raw_prefix`. Treat
          them as opaque strings; the guarantee is only that every claimed value is either in
          `field_offsets` or in `derived_fields`, which `provenance_check` now enforces.
          (2) A raw slice must be decoded with `ulpf.encoding.detected`, **not** UTF-8. Offsets are
          built against the codec the engine detected, so a Big5 or GB18030 event re-read as UTF-8
          slices to replacement characters. Found by fuzzing; `provenance_check` was wrong about this
          until A4 and is now fixed.
          Also: `engine_version` 0.3.0 → 0.4.0 (stamped on every event; the tier of an unregistered
          source changed, so rows should stay attributable to the engine that produced them), and
          `VEYRA_POISON_MAX_RETRIES` now has teeth — the in-flight batch is journalled to
          `data/state/<service>_inflight` so a record that kills the *process* is quarantined on
          restart rather than crash-looping, with one tier 4 `engine_crash` DLQ record per skipped
          message (P2: skipped, never silently dropped).
Why:      A4's whole point is that messy input becomes useful without inventing anything, and the
          two clarifications above are the places a downstream consumer would otherwise get it
          subtly wrong — showing no highlight instead of "derived", or highlighting the wrong bytes.
IDs:      IF-ULPF (clarification only), IF-ENGINE-LIB (`provenance_check` is stricter: it now reports
          unexplained claims, which is the second half the phase file always specified)
Files patched: track-A-dataplane/A4_tier3_offsets_robustness.md (status done, tasks/ACs ticked,
          Implementation notes), reports/A4.md, 06_STATUS_BOARD.md, track-A-dataplane/A5 and A6
          (downstream notes).
ACTION REQUIRED:
  - [ ] @B B6's highlighter: decode raw slices with `ulpf.encoding.detected`, and use
        `derived_fields` to explain a value that has no offset rather than showing nothing.
        `provenance_check(event, raw_bytes)` is exported and now covers both halves.
  - [ ] @C C3 and C4 can be built against real tier-3 records now, not stand-ins: `ulpf.template.sig`
        is stable for tier 3 (Drain3 has a key), `extract_tokens` and `mask()` are final, and
        `unmapped` carries every kv/JSON field the cascade found — that is the candidate set a draft
        should choose from. `mask()` keeps IPs on purpose.
  - [ ] @C REQUEST (stands from A3): `linux_sshd@1` covers 3 of the corpus shapes; the other 13 sshd
        lines are now tier 3 with observables rather than tier 4. Better, but still a gap in the
        seeded library pack.

## 2026-09-28 — C — DECISION + REQUEST @A  (contracts v1.3, no bump)
TYPE: DECISION
What:     The contract registry leaves this repository. It is now its own repository,
          github.com/arnavmahajan630/contracts-repo, checked out **beside** the code at
          `../contracts-repo` (Makefile `CONTRACTS_REPO=`, services `VEYRA_CONTRACTS_REPO`). Inside it
          the layout is unchanged: `<tenant_id>/<contract_id>.yaml`, a `seed` tag, one commit per
          contract version. `contracts-repo/` is removed from this repo, from .gitignore, from the
          ruff/mypy excludes and from CODEOWNERS.
Why:      Contracts are data with their own lifecycle (authored and approved by pack authors,
          committed at runtime), matching v1's separate Source Pack repository; keeping them out of
          the code repo stops runtime commits and resets from dirtying it.
IDs:      IF-CONTRACT-YAML (storage location only; the file format is unchanged), IF-API-CONTROL
          (/internal/reset wording)
Files patched: 00_MASTER.md (repo layout), 01_TEAM_GUIDE.md §1, 02_CONTRACTS.md (IF-CONTRACT-YAML,
          IF-API-CONTROL), track-C-control-console/{C00,C1,C2,C3}, track-B-evidence/B7_demo_engine.md,
          Makefile (contracts-repo-init), .gitignore, pyproject.toml, CODEOWNERS.
          Historical records (reports/S0.md, shared/S0_bootstrap.md, older entries here) are left as
          written.
Note:     No contracts version bump: the YAML format is untouched. If the team reads §0 as "a
          location change is breaking", bump to v1.4 and re-sync the headers.
ACTION REQUIRED:
  - [x] @A @B @C Clone the contracts repository next to `Veyra/` (same parent folder):
        `git clone https://github.com/arnavmahajan630/contracts-repo` — tests,
        `make contracts-repo-init` and the demo need it there.
  - [x] @A REQUEST: three A3 files still read the seed from inside this repo
        (`REPO / "contracts-repo" / "t_ntro_core"`): packages/veyra_engine/tests/test_golden.py,
        packages/veyra_engine/tests/test_invariants.py, tools/bench/engine_bench.py. Point them at
        `os.environ.get("VEYRA_CONTRACTS_REPO", REPO.parent / "contracts-repo")`. Until then those
        tests need the sibling checkout. (C checked: A3's golden suite passes unchanged with
        veyra_contracts.compile swapped in for mini_compile, 24/24.)
        <!-- done before A4: all four call sites (the fourth was tools/mock_control_publish.py) now
             use veyra_common.settings.contracts_repo_path(), which honours VEYRA_CONTRACTS_REPO and
             defaults to ../contracts-repo; the two test files skip with a clear message when the
             sibling checkout is missing. -->
  - [x] @C CI checks out the contracts repository (done 2026-09-28). It is private: an admin must
        add a `CONTRACTS_REPO_TOKEN` secret with read access, or make the repository public.
  - [ ] @B In B7, nothing changes in the API call (`/internal/reset` still resets the registry);
        the demo laptop just needs the checkout beside `Veyra/`.

## 2026-09-27 17:30 — A3 — VERSION-PIN  (contracts v1.2 → v1.3)
TYPE: VERSION-PIN
What:     `fastjsonschema` 2.21.2 added to veyra_engine and pinned in IF-VERSIONS. It compiles the
          vendored OCSF subset schema to Python once per class and validates an event in ~10 us;
          plain `jsonschema` measured ~216 us per event, about a third of the whole pipeline. Both
          read the same schema file, and `jsonschema` is kept to produce the full error list when an
          event really is invalid (off the hot path by definition).
Why:      A3's own risk list said "jsonschema is slow — cache validators and measure". Measured, so
          followed through. AC5 went from 1204-1480 EPS (below the 1500 target) to 2732 EPS on the
          slowest tier-1 shape.
IDs:      IF-VERSIONS
Files patched: 02_CONTRACTS.md (IF-VERSIONS + header v1.3), every plan file's contracts header,
          track-A-dataplane/A3_engine_core.md, 06_STATUS_BOARD.md, reports/A3.md.
ACTION REQUIRED:
  - [x] @C C2's golden-test runner validates compiled contracts against the same schema; use
        `veyra_engine.validate.validate_event` rather than calling jsonschema directly, so the
        compiled validator and its cache are shared.

## 2026-09-27 17:30 — A3 — CLARIFICATION  (contracts v1.3)
TYPE: CLARIFICATION
What:     IF-OCSF-SUBSET is now verified rather than assumed. Every class_uid, category_uid, activity
          id and the severity_id / status_id / disposition_id / action_id enums were checked against
          https://schema.ocsf.io/api/1.9.0/classes/<name> and all match the plan; type_uid =
          class_uid * 100 + activity_id holds.
          Two deliberate differences from upstream, recorded in packages/veyra_engine/ocsf/README.md:
          OCSF 1.9 marks `cloud` and `osint` **required** on several classes and VEYRA neither emits
          nor validates them (a pre-processor does not invent cloud metadata, and nothing downstream
          needs them); and the vendored schema constrains only the mapped field catalogue, leaving
          additionalProperties open, so its job is catching a wrong value rather than enumerating OCSF.
Why:      The plan said "verify ids against the pinned schema"; this is that verification, plus the
          honest note about what we deliberately do not enforce.
IDs:      IF-OCSF-SUBSET
Files patched: 02_CONTRACTS.md (IF-OCSF-SUBSET), reports/A3.md, packages/veyra_engine/ocsf/README.md.
ACTION REQUIRED:
  - [ ] @B In B6, note that events carry no `cloud`/`osint`; do not build a view that assumes them.
  - [x] @C In C4, the drafter's allowed-field catalogue is the same subset — keep it in step with
        packages/veyra_engine/ocsf/subset_1.9.0.json rather than re-listing fields by hand.
        `test_drafter_fields_match_the_vendored_subset` compares `catalogue.FIELDS` to the schema leaves.

## 2026-09-27 17:30 — A3 — REQUEST @C  (contracts v1.3)
TYPE: REQUEST
What:     The seeded `linux_sshd@1` library contract has three templates, and 13 of the 24 lines in
          demo/corpus/linux_sshd.log do not match any of them: pam_unix session open/close,
          `Accepted publickey`, `Received disconnect`, `Disconnecting invalid user`, `error: maximum
          authentication attempts exceeded`, sudo COMMAND, `Server listening`, `Received signal`.
          They are emitted correctly (never dropped) and land in the DLQ as `no_template_match`.
Why:      A owns the engine, not contracts-repo/, so A did not extend the contract. For the demo
          these shapes are either drift fodder for C3 or should be covered by the library pack —
          that is a C decision. Listing them here so nobody has to rediscover them.
IDs:      none (no interface changed)
Files patched: reports/A3.md, track-A-dataplane/A3_engine_core.md.
ACTION REQUIRED:
  - [x] @C In C3, decide per shape: extend linux_sshd's library pack, or leave it as drift the demo
        can show. `packages/veyra_engine/tests/expected/linux_sshd.log.json` lists exactly which
        lines fall through.

## 2026-09-27 15:40 — A1 — CLARIFICATION  (contracts v1.1 → v1.2)
TYPE: CLARIFICATION
What:     IF-INVENTORY's reload mechanism, measured on the pinned Vector 0.58.0: Vector watches
          the enrichment-table CSV itself under --watch-config, so a new row resolves within 5 s
          with NO reload.stamp touch and NO container restart. The atomic write-then-rename
          requirement stands; the stamp file stays as an inert hook. The A1 fallback (control-api
          restarting the edge via the Docker API) is not needed on this version.
          Caveat: a config reload tears the topology down and rebuilds it, and events arriving in
          that window are lost, so inventory writes should be batched.
Why:      A1 had to verify the mechanism rather than assume it; the answer is simpler than planned
          and removes work from C1.
IDs:      IF-INVENTORY
Files patched: 02_CONTRACTS.md (IF-INVENTORY + header v1.2), every plan file's contracts header,
          track-A-dataplane/A1_edge_collectors.md (implementation notes), 06_STATUS_BOARD.md,
          reports/A1.md, edge/RELOAD.md (new).
ACTION REQUIRED:
  - [ ] @C In C1, write sources.csv.tmp + atomic rename; skip the reload.stamp touch (harmless if
        kept) and do not restart the edge container. Batch inventory changes: a reload costs
        in-flight events.  <!-- C: atomic write and no restart done in C1; writes are not batched yet (reports/C1.md) -->
  - [ ] @B In B7's demo senders, give the containers static addresses on veyra_net if you want the
        (listener, peer_ip) resolution path exercised; from the host, peer_ip is the docker
        gateway, so only the syslog_host path is reachable.

## 2026-09-27 15:40 — A1 — DECISION  (contracts v1.2)
TYPE: DECISION
What:     Two Vector facts now encoded in code and profiles, not folklore: a disk buffer must be
          >= 256 MiB + 32 B (VEYRA_EDGE_BUFFER_BYTES is 268435488 and edge/render.py clamps to
          that floor — the old 268435456 crash-looped the edge), and a sink with a templated topic
          cannot run a healthcheck, so the kafka sink's healthcheck is disabled and container
          liveness comes from Vector's own API.
          Also: VRL length() is BYTE length, strlen() is characters. raw_len must use length(),
          or ulpf.field_offsets would be wrong for every non-ASCII source (the OT historian).
Why:      Each cost a debugging round; all three are pinned by tests now.
IDs:      none (no interface changed)
Files patched: profiles/*.env, packages/veyra_common/src/veyra_common/settings.py, edge/render.py,
          edge/vector/*, reports/A1.md.
ACTION REQUIRED:
  - [ ] @A In A6, when adding a second sink, remember the templated-topic healthcheck rule.

## 2026-09-27 05:10 — S0 — CLARIFICATION  (contracts v1.1)
TYPE: CLARIFICATION
What:     IF-WAZUH: rule 100100 must be a **child of Wazuh's built-in rule 99000**
          (`<if_sid>99000</if_sid>`), and 100110-100130 children of 100100. 99000
          ("Amazon Security Lake rules grouped", level 0) matches any json event carrying
          activity_id and category_uid, i.e. every OCSF event; a sibling rule loses to it and,
          because 99000 is level 0, produces no alert at all.
          IF-PORTS: immudb's pg wire is 5432 inside veyra_net; the host mapping is
          VEYRA_IMMUDB_PG_HOST_PORT (default 5433), because a local PostgreSQL usually owns 5432.
          The console host port is VEYRA_CONSOLE_PORT (default 8080, unchanged).
Why:      Found by running the stack: alerts were generated in alerts.json but rule 100100 never
          fired, and immudb could not bind 5432 on the demo laptop. No payload or schema changes.
IDs:      IF-WAZUH, IF-PORTS
Files patched: 02_CONTRACTS.md (IF-WAZUH, IF-PORTS), shared/S0_bootstrap.md (implementation notes),
          reports/S0.md.
ACTION REQUIRED:
  - [ ] @A In A6, define 100110-100130 with `<if_sid>100100</if_sid>` and verify each with
        wazuh-logtest before claiming the brute-force alert works.
  - [ ] @B In B3, connect to immudb's pg wire on host port 5433 (VEYRA_IMMUDB_PG_HOST_PORT), or
        on 5432 from inside the compose network.

## 2026-09-27 05:10 — S0 — REQUEST @C  (contracts v1.1)
TYPE: REQUEST
What:     The demo laptop's Ollama runs the model on **CPU, not GPU**: discovery reports only
          `library=cpu` although the NVIDIA driver 580.178.04, /dev/nvidia*, libcuda.so.1 and
          Ollama's own cuda_v12 runner are all present. Measured draft latency 53-91 s, against the
          5 s fallback window in 04_DEMO_SCRIPT §Beat 2. S0.5's "confirm 100% GPU" is therefore
          **not met**; AC5 (schema-valid JSON from inside a container) passes.
          Also: AC5 needs `OLLAMA_HOST=0.0.0.0:11434` (systemd drop-in), which exposes the LLM API
          on the LAN while Wi-Fi is on. The demo runs air-gapped, Wi-Fi off.
Why:      C4's live drafting and `make bench-llm` depend on GPU residency; until it is fixed the
          demo must rely on LLM_MODE=cache, which is a planned fallback but not the intended path.
IDs:      IF-VERSIONS (LLM model row)
Files patched: shared/S0_bootstrap.md (implementation notes), reports/S0.md, 06_STATUS_BOARD.md.
ACTION REQUIRED:
  - [x] @C Before C4's bench: get Ollama onto the GPU (or record CPU numbers honestly and set
        LLM_MODE=cache in profiles/laptop.env as the demo default).  <!-- C: 2026-09-29, 100% GPU on the RTX 4050; laptop stays on cache for accuracy, not speed -->
  - [x] @C Pin Drain3 (C3) and React/Vite/Tailwind (C5) with a VERSION-PIN entry each; S0 left  <!-- C: Drain3 pinned 2026-09-28; React stack 2026-09-29 (C5) -->
        those rows open rather than guessing.

## 2026-09-26 18:30 — S0 — VERSION-PIN  (contracts v1.0 → v1.1)
TYPE: VERSION-PIN
What:     IF-VERSIONS filled with the exact versions running on the demo laptop: Python 3.12.3,
          uv 0.12.19, apache/kafka:4.1.2 (KRaft single node), confluent-kafka 2.15.1,
          timberio/vector:0.58.0-debian, clickhouse/clickhouse-server:25.8 (LTS),
          codenotary/immudb:1.11.2-bullseye-slim, Wazuh 4.14.8, Ollama 0.20.3 + qwen2.5:3b,
          OCSF 1.9.0, caddy:2.11.4-alpine, google-re2 1.1.20251105, Node 25.2.1.
          Drain3 and the React/Vite/Tailwind majors are deliberately left to C3 and C5,
          because no S0 code imports them and pinning them blind would be a guess.
Why:      S0.6: every later phase must build against the same versions, and `uv.lock` plus
          these image tags are what makes the demo reproducible on another machine.
IDs:      IF-VERSIONS
Files patched: 02_CONTRACTS.md (IF-VERSIONS + header v1.1), every track/shared/phase file's
          `contracts:` header (additive change, no semantic effect), shared/S0_bootstrap.md task 22.
ACTION REQUIRED:
  - [ ] @B Use `codenotary/immudb:1.11.2-bullseye-slim` for B3; the pg-wire port is published
        on 5432 and `IMMUDB_PGSQL_SERVER=true` is already set in compose.
  - [x] @C Pin Drain3 in C3 and React/Vite/Tailwind in C5, then add a VERSION-PIN entry each.  <!-- C: Drain3 done 2026-09-28; React stack 2026-09-29 -->

## 2026-09-26 18:30 — S0 — CLARIFICATION  (contracts v1.1)
TYPE: CLARIFICATION
What:     Node is pinned at **25.2.1**, not the 20/22 LTS line IF-VERSIONS originally asked for.
          Recorded as decision D17 in 00_MASTER §8, with the revisit condition (a Vite or
          Tailwind major refusing Node 25 → build in a `node:22` container).
Why:      25.2.1 is the toolchain on the demo laptop, and per D2 there is no Node at runtime:
          the console ships as a static bundle served by Caddy, so the build-time major is
          never exposed to the demo. One toolchain on the laptop and in CI beats two.
IDs:      IF-VERSIONS
Files patched: 02_CONTRACTS.md (IF-VERSIONS Node row), 00_MASTER.md (D17),
          track-C-control-console/C5_console_shell_overview.md (task 1),
          track-C-control-console/C6_console_pages.md (task 1), .github/workflows/ci.yml.
ACTION REQUIRED:
  - [x] @C In C5, set `"engines": {"node": ">=25"}` in console/package.json and commit
        package-lock.json; CI's `console` job runs `npm ci && npm run build` on Node 25.

## 2026-09-26 18:30 — S0 — DECISION  (contracts v1.1)
TYPE: DECISION
What:     S0 was executed by **Person A alone**, including B's and C's S0 shares. Attribution
          per item is in `reports/S0.md`; in short — A: veyra_common, veyra_engine stub, corpus,
          wazuh rules/config. A-for-B: compose (base + wazuh + secure + obs), profiles, Caddyfile,
          Makefile, fake_raw.py/fake_norm.py. A-for-C: repo layout, uv workspace, ruff/pytest/mypy
          config, CODEOWNERS, pre-commit, CI, tools/plan_check.py, contracts-repo seed, Ollama setup.
Why:      S0 is jointly owned and unblocks every track; nothing could start otherwise. Team
          ownership from 01_TEAM_GUIDE §1 is unchanged from A1/B1/C1 onward.
IDs:      none (no interface changed by this entry)
Files patched: 06_STATUS_BOARD.md, shared/S0_bootstrap.md (implementation notes).
ACTION REQUIRED:
  - [ ] @B Review and adopt your S0 deliverables before starting B1/B2: compose/*, profiles/*,
        Makefile, Caddyfile, demo/tools/fake_*.py. Anything you would have built differently is
        yours to change — you own those directories from now on.
  - [ ] @C Review and adopt yours before starting C1: pyproject.toml workspace, CODEOWNERS,
        .pre-commit-config.yaml, .github/workflows/ci.yml, tools/plan_check.py, contracts-repo/
        seed contracts, and the Ollama host setup (model qwen2.5:3b).
  - [x] @A Fix the CODEOWNERS placeholder handles (@person-a/@person-b/@person-c) once the repo
        has a remote.  <!-- done in A1: A is @arnavmahajan630; B and C stay placeholders until
        their GitHub accounts are known -->

## 2026-09-26 — PLAN — DECISION  (contracts v1.0)
What:     Initial plan published. Decisions D1–D16 in 00_MASTER §8. Contracts v1.0.
Why:      Kickoff.
IDs:      all
Files patched: all
ACTION REQUIRED:
  - [x] @A @B @C Read 00_MASTER, 01_TEAM_GUIDE, 04_DEMO_SCRIPT before S0.  <!-- A: read all 34 plan files before starting S0 -->
  - [x] @A @B @C Run S0 together; fill IF-VERSIONS; log a VERSION-PIN entry.  <!-- run solo by A; see the S0 DECISION entry above -->
