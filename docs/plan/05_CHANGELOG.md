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
  - [ ] @A A2: hash API-key secrets exactly as IF-CONTROL now states,
        `sha256(pepper_bytes + secret_utf8)`, and match on `pepper_id`.
  - [ ] @A A5: set `replay_job_id` on `lineage` records for `replay.raw` input. control-api's replay
        progress counts exactly those records; without them every replay job times out.
  - [ ] @A A3: `tools/mock_control_publish.py` can go; control-api now publishes `control`.
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
  - [ ] @C Pin React/Vite/Tailwind when C5 lands in `console/` (Drain3 is now done).

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
  - [ ] @A @B @C Clone the contracts repository next to `Veyra/` (same parent folder):
        `git clone https://github.com/arnavmahajan630/contracts-repo` — tests,
        `make contracts-repo-init` and the demo need it there.
  - [x] @A REQUEST (done by C 2026-09-28, please review): the move merged before A switched, so
        C patched the three A3 files that still read the seed from inside this repo
        (`REPO / "contracts-repo" / "t_ntro_core"`): packages/veyra_engine/tests/test_golden.py,
        packages/veyra_engine/tests/test_invariants.py, tools/bench/engine_bench.py. Point them at
        `os.environ.get("VEYRA_CONTRACTS_REPO", REPO.parent / "contracts-repo")`. Until then those
        tests need the sibling checkout. (C checked: A3's golden suite passes unchanged with
        veyra_contracts.compile swapped in for mini_compile, 24/24.)
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
  - [ ] @C In C4, the drafter's allowed-field catalogue is the same subset — keep it in step with
        packages/veyra_engine/ocsf/subset_1.9.0.json rather than re-listing fields by hand.

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
  - [ ] @C Before C4's bench: get Ollama onto the GPU (or record CPU numbers honestly and set
        LLM_MODE=cache in profiles/laptop.env as the demo default).
  - [ ] @C Pin Drain3 (C3) and React/Vite/Tailwind (C5) with a VERSION-PIN entry each; S0 left  <!-- C: Drain3 pinned 2026-09-28; React stack with C5 -->
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
  - [ ] @C Pin Drain3 in C3 and React/Vite/Tailwind in C5, then add a VERSION-PIN entry each.  <!-- C: Drain3 done 2026-09-28 -->

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
  - [ ] @C In C5, set `"engines": {"node": ">=25"}` in console/package.json and commit
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
