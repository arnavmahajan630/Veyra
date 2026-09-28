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
  - [ ] @C Run `make bench-llm MODELS=qwen2.5:3b,llama3.2:3b` when a GPU is available, and set
        `VEYRA_LLM_MODEL` from the winner.

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
  - [ ] @C C5/C6 should call these bodies, not the old `{tenant_id, source_name, transport}` analyze sketch.

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
