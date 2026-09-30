# B7 — Demo engine: scenario runner, reset, stages, preflight, demo panel

```
track: B   owner: B   status: done
contracts: v1.5
depends_on: [B5, C1 (/internal/reset, /internal/demo/last-key), A6 (saved objects)]   unblocks: [CP4, S2]
consumes: [IF-API-DEMO, IF-API-CONTROL (internal), IF-PORTS, IF-TOPICS, IF-CH-SCHEMA, IF-WAZUH]
provides: [services/demo_engine, demo/scenarios/*.yaml, make demo-*, console /demo page]
directories: [services/demo_engine/, demo/, console/src/pages/demo/, Makefile (demo-* targets)]
```

## Goal

Make the 3-minute demo **reproducible on command**. The demo engine:
- generates realistic traffic over the real ingestion paths (syslog UDP/TCP to Vector, HTTP push to the gateway with the key issued live);
- runs the scripted stages from `04_DEMO_SCRIPT.md`;
- resets the whole system to the pre-demo state in under 90 s;
- runs preflight checks;
- serves the hidden `/demo` panel and global hotkeys.

## Design

### Scenario format: `demo/scenarios/sih_main.yaml`
```yaml
scenario: sih_main
seed: 42                              # all randomness (jitter, sample choice) derives from this
baseline:
  - {name: ntro_fw,  via: syslog_tcp, listener: dmz-tcp,  corpus: acme_ngfw_cef.log, eps: ${DEMO_EPS_BASELINE}*0.5}
  - {name: ntro_lnx, via: syslog_udp, listener: core-udp, corpus: linux_sshd.log,    eps: ${DEMO_EPS_BASELINE}*0.5,
     exclude_patterns: ["Failed password"]}     # keep brute force for stage 3 only
stages:
  1: {title: "Hook", actions: []}
  2: {title: "Onboard Auth Server", actions: [{prefill_onboarding: {samples: [authsrv_t1_ok.log#1, authsrv_t2_session.log#1, authsrv_t1_ok.log#2]}}]}
  3:
    title: "Log storm"
    actions:
      - {send: {via: http_hec_event, key: live, corpus: authsrv_t1_ok.log, count: 20, over_s: 20}}
      - {send: {via: http_hec_event, key: live, corpus: authsrv_t2_session.log, count: 10, over_s: 20}}
      - {send: {via: http_hec_event, key: live, corpus: authsrv_t3_failed.log, count: 8, over_s: 20,
                vary: {user: [a.sharma], src_ip: [103.21.4.77]}}}
      - {send: {via: syslog_udp, listener: core-udp, template: "sshd_failed", count: 7, over_s: 15, vary: {src_ip: [45.12.3.9]}}}
      - {send: {via: syslog_tcp, listener: dmz-tcp, corpus: acme_ngfw_cef.log, filter: "act=deny", count: 30, over_s: 20}}
      - {send: {via: syslog_udp, listener: core-udp, corpus: ot_historian.log, count: 12, over_s: 20, host: ot-hist-01}}
    expect:                                        # used by demo-auto and CP4
      - {within_s: 25, clickhouse: "tier=3 AND source_id='src_authsrv_01'", gte: 8}
      - {within_s: 30, wazuh_rule: 100111, src_ip: 45.12.3.9}
  4:
    title: "Drift loop"
    actions: [{drift_flush: {}}, {resend_if_no_drift: {after_s: 10, corpus: authsrv_t3_failed.log, count: 3}}]
    expect: [{within_s: 10, drift_open_for: src_authsrv_01}]
  5: {title: "Traceability", actions: []}
  6: {title: "Close", actions: []}
auto:                                               # demo-auto script: human clicks simulated via APIs
  - {at: 0,   stage: 1}
  - {at: 15,  api: onboarding_flow}                 # analyze → draft → submit → approve (approver) → key
  - {at: 40,  stage: 3}
  - {at: 85,  stage: 4}
  - {at: 95,  api: drift_approve_promote_replay}
  - {at: 130, api: verify_then_tamper_then_verify}
```

- **Corpus references:** `file#n` picks line n. `template:` names a generator in `demo/generators.py` (e.g. `sshd_failed` builds a proper RFC 3164 sshd line with the current time).
- **Timestamps** in corpus lines are rewritten to "now" in their own format (keeping the format), so time parsing and skew look real. The rewrite must not change the line's structure.
- **`vary`** substitutes named values deterministically.
- **`key: live`** fetches `GET control-api /internal/demo/last-key` (demo mode only) at stage start; fails loudly if no key exists yet.

### Senders
- Syslog UDP/TCP over sockets to the edge listeners. TCP lines are newline-terminated; multi-line T3 events keep their continuation line (the edge joins it).
- **HEC event:** one JSON object per event, with the multi-line string embedded.
- **Host fingerprints:** the syslog host field drives `syslog_host` resolution (IF-INVENTORY), so one container simulates all devices.
- **Host values per device:**
  - CEF lines are wrapped in an RFC 3164 header with host `fw-dmz-01` (common real-world practice);
  - sshd lines use host `core-lnx-07`;
  - OT historian lines use host `ot-hist-01`, which is deliberately absent from the inventory, so it lands as unregistered.

  These must match C1's seed rows.
- **Rate control:** a monotonic scheduler with `seed`-based jitter. Stages are **idempotent**: re-triggering a stage while it runs is a no-op; after it finishes, re-triggering runs it again (useful in the fallback "press again").

### Reset (`POST /reset`, `make demo-reset`), with step timings recorded
1. Pause baseline traffic.
2. `control-api /internal/reset {scenario: sih_main}`: SQLite wiped and reseeded (tenants, users, NTRO sources, library contracts active, no Maha Power source); the contract registry (`../contracts-repo`, a separate repository) reset to the `seed` tag; <!-- synced from C: registry moved out of the code repo (05_CHANGELOG, 2026-09-27) --> control topic republished; inventory CSV rewritten.
3. Kafka: delete and recreate all topics except `control` (IF-TOPICS, profile partitions). The admin client waits for completion. `control` is compacted and rewritten by step 2; delete/recreate it **before** step 2 if its tombstones would confuse consumers, and test which order works.
4. ClickHouse: `TRUNCATE` every table in `veyra` (MVs included).
5. Vault: remove segments, ledger, integrity head, tamper backups. **Keep `data/keys/`.**
6. immudb: write a new database name `veyra_<unix>` to `data/state/immudb_db` and create the database.
7. Wazuh: delete today's alert and archive indices matching the VEYRA groups via the indexer API; truncate the sink files; re-import the saved objects (A6).
8. Restart the stateful consumers (normalizer, archiver, integrity, lineage-indexer, router, drift-worker) through the Docker API, so they reload clean state and groups. The demo-engine container mounts the Docker socket, **demo profile only**; declare this in the master deviations if asked.
9. Resume baseline. Pre-warm:
   - one verify on a baseline event once a segment seals (the reset waits up to `SEGMENT_MAX_SECONDS`, or skips if the budget would exceed 90 s);
   - console API calls;
   - `ollama` keep-alive ping (`/api/generate` with an empty prompt).
10. Return a summary `{ok, seconds, steps:[{name, ms}]}`.

### Preflight (`GET /preflight`, `make demo-preflight`)
Checks (each PASS/WARN/FAIL with detail):
- every container healthy;
- host memory free ≥ 3 GB (read via `/proc/meminfo` on the host mount, or `docker info`);
- Ollama model loaded (`/api/ps`) and on GPU;
- the LLM cache contains the draft for the T3 `template_sig` (computed with `veyra_engine.template_sig`);
- Wazuh rules 100100–100130 present (manager API);
- the time offset between containers is < 1 s;
- disk free ≥ 10 GB;
- no segment open longer than 2× max seconds;
- baseline EPS within ±30% of target;
- the `/demo` hotkeys registered (console reports via a ping endpoint).

### Demo panel: console route `/demo` (hidden from the nav; visible in demo mode)
- Stage buttons 1–6 with status (idle, running, done) and the stage's `expect` results live.
- Reset button with confirmation, plus a progress list of the reset steps.
- The preflight results table.
- Tamper buttons for each B5 mode, plus Untamper, for the currently selected event (a picker defaults to the last replayed event).
- The script beat timer: start/stop, current beat, and elapsed vs target, to help rehearsals.
- **Global hotkeys** via C5's registry: `Shift+1..6` stages, `Shift+T` tamper (B6 owns the verify re-run), `Shift+R` reset (confirmation modal). Active only when `DEMO_MODE=1`.

### `make demo-auto`
Runs the `auto:` script against the live system. Each `api:` step drives the real public APIs, exactly as a human clicking would:
- `onboarding_flow`: login as author, analyze, draft, submit, switch to approver, approve, fetch key;
- `drift_approve_promote_replay`;
- `verify_then_tamper_then_verify`.

It evaluates every `expect` and prints a PASS/FAIL table with timings. CP4 runs it 10 times: `make demo-auto N=10`.

## Tasks
- [x] 1. Service skeleton + scenario loader + validator (unknown corpus refs, bad stage ids → error at load).
- [x] 2. Generators + timestamp rewriter + senders (UDP, TCP, HEC) + rate scheduler.
- [x] 3. Baseline loop; stages; idempotence.
- [x] 4. Reset orchestration with timings; tune until < 90 s on the laptop.
- [x] 5. Preflight.
- [x] 6. The `/demo` panel page + hotkeys (via C5's registry).
- [x] 7. `demo-auto` runner + expectation evaluators (ClickHouse, Wazuh indexer, control API).
- [x] 8. Makefile targets: `demo-reset`, `demo-preflight`, `demo-stage N=`, `demo-auto [N=]`, `llm-warm` (proxy to C4's warm call).

## Acceptance criteria
- [ ] AC1: `make demo-reset` < 90 s on the laptop profile; the post-reset state matches
      `04_DEMO_SCRIPT.md` §2 exactly. **Deferred to the rehearsal** — needs the full profile. The
      budget is enforced in code (`VEYRA_DEMO_RESET_BUDGET_S`, `over_budget` in the summary) and
      the step ordering and failure propagation are unit-tested against fakes.
- [ ] AC2: **deferred to CP4** — the runner, the three real API flows and the evaluators are
      built and unit-tested; ten live runs need the stack.
- [x] AC3: Hotkeys trigger stages from any console page; a double press doesn't double-send.
- [~] AC4: With Ollama stopped, `demo-auto` still passes (cache fallback) and preflight reports
      WARN, not FAIL. The **preflight half passes** (unit-tested against a refused connection);
      the `demo-auto` half rides on AC2 and the rehearsal.

## Settings
`VEYRA_DEMO_MODE` (1 on demo machines), `VEYRA_DEMO_SCENARIO` (`sih_main`), `VEYRA_DEMO_EPS_BASELINE`, `VEYRA_DEMO_RESET_BUDGET_S` (90).

## Implementation notes

Done 2026-09-30 apart from the two live ACs. Full write-up in [reports/B7.md](../reports/B7.md).

The first pass had the shape right and the substance wrong in ways that would only have shown up
on stage:

- **Three places reported success unconditionally.** Preflight returned fixed `PASS` strings for
  the Wazuh rules, the vault and the clock; `_evaluate_expectation` returned `True` for any
  clause it did not recognise, so stage 3's Wazuh 100111 expectation had never been evaluated;
  and every reset step was wrapped in a swallow-all `except` with `ok` hardcoded true.
- **Multi-line events were shredded** — the T3 stack-trace line was sent as its own event, so
  Beat 5's subject event did not exist. Corpora are now parsed into events.
- **The baseline would have fired rule 100111 before Beat 3**, because the `Failed password`
  exclusion the phase file specifies had been dropped from the scenario. Restored.
- **Half the scenario grammar was declared and never read** (`filter`, `host`, `file#n`,
  `exclude_patterns`, and no `seed` at all), and the OT and CEF timestamp formats were never
  rewritten.
- **Six reset steps did not do what their names said** — 6 hardcoded Kafka topics instead of
  IF-TOPICS, 8 hardcoded ClickHouse tables, an immudb step that only wrote a filename, no Wazuh
  index cleanup or saved-object re-import, a restart list missing archiver and integrity, and no
  pre-warm.
- **Nothing was reachable**: the `make demo-*` targets were `@echo "TODO (B7)"`, `demo-auto` did
  not exist, and there was no `demo-engine` service in compose — though Caddy, Prometheus and the
  Dockerfile all already pointed at it.

The scenario moved to its contracted path `demo/scenarios/sih_main.yaml`. Deviations: the docker
socket is mounted (demo profile only, over the Engine API with httpx rather than the SDK, whose
import name the repo's own `docker/` directory shadows); and the OT historian is sent
byte-for-byte with no `host:` override, because those bytes are what A4's tier-3 goldens and
S0's parity vectors are computed from — the validator now rejects a `host:` there instead of
ignoring it.

Verified by 47 pytest tests and 9 `DemoPage` tests, none of which need the stack.
