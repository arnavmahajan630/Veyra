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
  - [ ] @C Pin Drain3 in C3 and React/Vite/Tailwind in C5, then add a VERSION-PIN entry each.

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
  - [ ] @A Fix the CODEOWNERS placeholder handles (@person-a/@person-b/@person-c) once the repo
        has a remote.

## 2026-09-26 — PLAN — DECISION  (contracts v1.0)
What:     Initial plan published. Decisions D1–D16 in 00_MASTER §8. Contracts v1.0.
Why:      Kickoff.
IDs:      all
Files patched: all
ACTION REQUIRED:
  - [x] @A @B @C Read 00_MASTER, 01_TEAM_GUIDE, 04_DEMO_SCRIPT before S0.  <!-- A: read all 34 plan files before starting S0 -->
  - [x] @A @B @C Run S0 together; fill IF-VERSIONS; log a VERSION-PIN entry.  <!-- run solo by A; see the S0 DECISION entry above -->
