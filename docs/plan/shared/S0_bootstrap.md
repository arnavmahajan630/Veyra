# S0 — Bootstrap (all three together)

```
track: shared   owner: A+B+C   status: todo
contracts: v1.1
depends_on: []                      unblocks: [every phase]
consumes: [IF-TOPICS, IF-PORTS, IF-NAMING, IF-ENV, IF-VERSIONS, IF-ENVELOPE, all record IFs]
provides: [repo, compose, profiles, veyra_common, stubs, corpus, CI, pinned versions]
directories: [everything]
```

## Goal

Create a repo where every service can boot, talk to Kafka, and be tested, with stubs, so the three tracks can build in parallel without waiting on each other. Pin every version.

Split the S0 work across the three people as shown in the task list; each person drives an agent on their share. Integrate at the end of S0, together.

## Tasks

### S0.1 Repo and tooling (Person C drives)
- [ ] 1. `git init`; create the layout from `00_MASTER.md` §9. Copy this plan folder to `docs/plan/`.
- [ ] 2. `uv` workspace: root `pyproject.toml` with members `packages/*` and `services/*`, Python 3.12. Dev tools: ruff, pytest, pytest-asyncio, mypy (lenient).
- [ ] 3. `.gitignore` (`data/`, `.env.local`, `node_modules`, `dist`); `CODEOWNERS` per `01_TEAM_GUIDE.md` §1; pre-commit with ruff.
- [ ] 4. CI (GitHub Actions or local `make ci`): lint, unit tests, `plan-check`. Integration tests run locally with `make test-int`.
- [ ] 5. `tools/plan_check.py`: parses the `contracts: vX.Y` header of every plan file, compares it to `02_CONTRACTS.md`, and lists stale files. Wire it to `make plan-check`.

### S0.2 `veyra_common` (Person A drives)
- [ ] 6. `settings.py`: a `Settings` (pydantic-settings, prefix `VEYRA_`) containing **every knob** in `03_INFRA_PROFILES.md` §2, with laptop defaults. Services subclass or compose it with their own sections.
- [ ] 7. `models/`: Pydantic v2 models for IF-ENVELOPE, IF-LINEAGE, IF-DLQ, IF-SHADOW, IF-VAULT-INDEX, IF-RECEIPT, IF-AUDIT, IF-CONTROL values, and a loose IF-NORM-EVENT (OCSF dict + typed `ulpf`).
  - Each model gets a JSON fixture in `packages/veyra_common/fixtures/` and a round-trip test.
  - The owning track later edits its models via PR.
- [ ] 8. `topics.py`: topic names, category mapping, and a `create_topics()` admin function driven by profile partitions, retention and compaction. Wire it to `make topics`.
- [ ] 9. `kafka.py`:
  - `make_producer(transactional_id=None)` with acks=all, idempotence and zstd;
  - `make_consumer(group, topics or pattern)` with read_committed and manual commits;
  - a `TxnProcessor` helper: consume batch → `fn(records)` → produce outputs → `send_offsets_to_transaction` → commit, with abort-and-retry on error.
- [ ] 10. `ids.py`: UUIDv7 (library such as `uuid-utils` or `uuid6`) and `now_ns()`. `hashing.py`: `sha256_hex`, plus the `template_sig` reference implementation copied from `reference/spec_vectors.py`, with tests against its vectors.
- [ ] 11. `service.py`: `ServiceApp` base. It provides:
  - JSON logging;
  - a Prometheus `/metrics` endpoint and `/healthz` on the service's metrics port;
  - SIGTERM → graceful stop hooks;
  - a readiness gate.

### S0.3 Compose, profiles, Makefile (Person B drives)
- [ ] 12. `compose/docker-compose.yml`:
  - `kafka` (KRaft single node), `clickhouse`, `immudb`, `caddy`;
  - `edge-dmz` and `edge-core` (Vector, placeholder config that forwards to `raw.unregistered`);
  - one Python image, `docker/python.Dockerfile` (uv, non-root, slim), reused by every service with a different `command`;
  - every service's `mem_limit` from `${VEYRA_MEM_*}`;
  - `extra_hosts: host.docker.internal:host-gateway`.
- [ ] 13. `compose/docker-compose.wazuh.yml`, based on the official single-node Wazuh docker deployment:
  - certificate generation as a `make wazuh-certs` step;
  - indexer heap set from `${VEYRA_WAZUH_INDEXER_HEAP}`;
  - volume `./data/sinks/wazuh` mounted into the manager at `/sinks/wazuh`.

  Confirm the dashboard login works.
- [ ] 14. `profiles/laptop.env`, `mac.env`, `workstation.env` with every knob. `make up PROFILE=… [WAZUH=local|remote]` merges files and runs compose.
- [ ] 15. `Caddyfile`: routes per IF-PORTS. The console dev server is proxied during development; the static build in the demo.
- [ ] 16. Makefile targets:
  - fully implemented: `up`, `down`, `ps`, `logs S=`, `topics`, `wipe-data`, `test`, `test-int`, `lint`, `plan-check`, `wazuh-certs`;
  - stubs that print TODO with the owning phase: `e2e-smoke`, `llm-warm`, `bench-llm`, `bench-throughput`, `demo-reset`, `demo-preflight`, `demo-stage N=`.

### S0.4 Stubs and corpus (all)
- [ ] 17. `packages/veyra_engine` **stub** (A): implements the IF-ENGINE-LIB signatures.
  - `normalize()` returns tier 4 with `raw_data`, and a full `ulpf` except `field_offsets`.
  - `template_sig()` and `extract_tokens()` are real (simple tokens: kv pairs, IPs, words).
  - Stub or real, the signatures never change.
- [ ] 18. `demo/tools/fake_raw.py` (B): produces valid envelopes from `demo/corpus/` files to `raw.<vendor>` at a given EPS. `demo/tools/fake_norm.py`: produces IF-NORM-EVENT tier 1/3 fixtures to `norm.*`. These let B and C work without A.
- [ ] 19. `demo/corpus/` (all, 15 minutes each). Real-looking samples, at least 20 lines per source type:

| File | Contents |
|---|---|
| `linux_sshd.log` | Failed and Accepted password lines, invalid user, disconnect |
| `acme_ngfw_cef.log` | CEF traffic allow and deny |
| `authsrv_t1_ok.log`, `authsrv_t2_session.log`, `authsrv_t3_failed.log` | Exactly as in `04_DEMO_SCRIPT.md` §2.1, including the multi-line stack trace |
| `ot_historian.log` | Weird fixed-width legacy lines, semicolon-separated, with a Hindi tag field |
| `garbage.bin` | Binary noise, invalid UTF-8, an oversize line (200 KB), an empty line, a 1-byte line |

- [ ] 20. `contracts-repo/` seeded (C): git-init it. Add placeholder library contracts `linux_sshd.yaml` and `acme_ngfw_cef.yaml` in IF-CONTRACT-YAML format; C2/C3 finalize them.

### S0.5 LLM host setup (Person C)
- [ ] 21. Install Ollama natively and pull the laptop model.
  - Run `ollama ps` during a request and confirm the model is **100% GPU**.
  - Send one structured-output request (JSON schema) and confirm it returns valid JSON.
  - Record VRAM usage.

### S0.6 Pin and publish
- [x] 22. Fill **IF-VERSIONS** in `02_CONTRACTS.md` with the exact versions and tags in use. Add a `VERSION-PIN` changelog entry. <!-- Node pinned at 25.2.1, not an LTS line: decision D17 -->
- [ ] 23. Everyone runs the acceptance checks on the **demo laptop**.

## Acceptance criteria

- [ ] AC1: `make up PROFILE=laptop` brings every container to healthy within 3 minutes. `docker stats` total is ≤ 9 GB (no Ollama, idle).
- [ ] AC2: `make topics` creates every IF-TOPICS topic with profile partitions. `kafka-topics --describe` output is saved in the report.
- [ ] AC3: `fake_raw.py --eps 50` for 60 s; a throwaway consumer counts 3000 valid envelopes. `veyra_common` model validation passes on all of them.
- [ ] AC4: The Wazuh dashboard opens at `https://localhost:8443`. A manual NDJSON line appended to `data/sinks/wazuh/veyra.ndjson` appears in Discover with the temporary rule 100100 (A pre-creates a minimal `veyra_rules.xml`).
- [ ] AC5: The Ollama JSON-schema request succeeds from **inside a container**, via `host.docker.internal`.
- [ ] AC6: `make test` and `make lint` are green. The `template_sig` vectors pass.
- [ ] AC7: `make plan-check` runs and reports zero stale files.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Wazuh cert generation friction | Follow the official single-node guide exactly; timebox it; one person owns it |
| Kafka KRaft single-node config | Use the official image's documented env for a combined broker/controller |
| `host-gateway` not resolving | Use the docker bridge IP (`172.17.0.1`) in `.env.local` |

## Implementation notes
_(filled after execution)_
