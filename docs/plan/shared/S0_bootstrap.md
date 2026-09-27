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
- [x] 1. `git init`; create the layout from `00_MASTER.md` §9. Copy this plan folder to `docs/plan/`.
- [x] 2. `uv` workspace: root `pyproject.toml` with members `packages/*` and `services/*`, Python 3.12. Dev tools: ruff, pytest, pytest-asyncio, mypy (lenient).
- [x] 3. `.gitignore` (`data/`, `.env.local`, `node_modules`, `dist`); `CODEOWNERS` per `01_TEAM_GUIDE.md` §1; pre-commit with ruff.
- [x] 4. CI (GitHub Actions or local `make ci`): lint, unit tests, `plan-check`. Integration tests run locally with `make test-int`.
- [x] 5. `tools/plan_check.py`: parses the `contracts: vX.Y` header of every plan file, compares it to `02_CONTRACTS.md`, and lists stale files. Wire it to `make plan-check`.

### S0.2 `veyra_common` (Person A drives)
- [x] 6. `settings.py`: a `Settings` (pydantic-settings, prefix `VEYRA_`) containing **every knob** in `03_INFRA_PROFILES.md` §2, with laptop defaults. Services subclass or compose it with their own sections.
- [x] 7. `models/`: Pydantic v2 models for IF-ENVELOPE, IF-LINEAGE, IF-DLQ, IF-SHADOW, IF-VAULT-INDEX, IF-RECEIPT, IF-AUDIT, IF-CONTROL values, and a loose IF-NORM-EVENT (OCSF dict + typed `ulpf`).
  - Each model gets a JSON fixture in `packages/veyra_common/fixtures/` and a round-trip test.
  - The owning track later edits its models via PR.
- [x] 8. `topics.py`: topic names, category mapping, and a `create_topics()` admin function driven by profile partitions, retention and compaction. Wire it to `make topics`.
- [x] 9. `kafka.py`:
  - `make_producer(transactional_id=None)` with acks=all, idempotence and zstd;
  - `make_consumer(group, topics or pattern)` with read_committed and manual commits;
  - a `TxnProcessor` helper: consume batch → `fn(records)` → produce outputs → `send_offsets_to_transaction` → commit, with abort-and-retry on error.
- [x] 10. `ids.py`: UUIDv7 (library such as `uuid-utils` or `uuid6`) and `now_ns()`. `hashing.py`: `sha256_hex`, plus the `template_sig` reference implementation copied from `reference/spec_vectors.py`, with tests against its vectors.
- [x] 11. `service.py`: `ServiceApp` base. It provides:
  - JSON logging;
  - a Prometheus `/metrics` endpoint and `/healthz` on the service's metrics port;
  - SIGTERM → graceful stop hooks;
  - a readiness gate.

### S0.3 Compose, profiles, Makefile (Person B drives)
- [x] 12. `compose/docker-compose.yml`:
  - `kafka` (KRaft single node), `clickhouse`, `immudb`, `caddy`;
  - `edge-dmz` and `edge-core` (Vector, placeholder config that forwards to `raw.unregistered`);
  - one Python image, `docker/python.Dockerfile` (uv, non-root, slim), reused by every service with a different `command`;
  - every service's `mem_limit` from `${VEYRA_MEM_*}`;
  - `extra_hosts: host.docker.internal:host-gateway`.
- [x] 13. `compose/docker-compose.wazuh.yml`, based on the official single-node Wazuh docker deployment:
  - certificate generation as a `make wazuh-certs` step;
  - indexer heap set from `${VEYRA_WAZUH_INDEXER_HEAP}`;
  - volume `./data/sinks/wazuh` mounted into the manager at `/sinks/wazuh`.

  Confirm the dashboard login works.
- [x] 14. `profiles/laptop.env`, `mac.env`, `workstation.env` with every knob. `make up PROFILE=… [WAZUH=local|remote]` merges files and runs compose.
- [x] 15. `Caddyfile`: routes per IF-PORTS. The console dev server is proxied during development; the static build in the demo.
- [x] 16. Makefile targets:
  - fully implemented: `up`, `down`, `ps`, `logs S=`, `topics`, `wipe-data`, `test`, `test-int`, `lint`, `plan-check`, `wazuh-certs`;
  - stubs that print TODO with the owning phase: `e2e-smoke`, `llm-warm`, `bench-llm`, `bench-throughput`, `demo-reset`, `demo-preflight`, `demo-stage N=`.

### S0.4 Stubs and corpus (all)
- [x] 17. `packages/veyra_engine` **stub** (A): implements the IF-ENGINE-LIB signatures.
  - `normalize()` returns tier 4 with `raw_data`, and a full `ulpf` except `field_offsets`.
  - `template_sig()` and `extract_tokens()` are real (simple tokens: kv pairs, IPs, words).
  - Stub or real, the signatures never change.
- [x] 18. `demo/tools/fake_raw.py` (B): produces valid envelopes from `demo/corpus/` files to `raw.<vendor>` at a given EPS. `demo/tools/fake_norm.py`: produces IF-NORM-EVENT tier 1/3 fixtures to `norm.*`. These let B and C work without A.
- [x] 19. `demo/corpus/` (all, 15 minutes each). Real-looking samples, at least 20 lines per source type:

| File | Contents |
|---|---|
| `linux_sshd.log` | Failed and Accepted password lines, invalid user, disconnect |
| `acme_ngfw_cef.log` | CEF traffic allow and deny |
| `authsrv_t1_ok.log`, `authsrv_t2_session.log`, `authsrv_t3_failed.log` | Exactly as in `04_DEMO_SCRIPT.md` §2.1, including the multi-line stack trace |
| `ot_historian.log` | Weird fixed-width legacy lines, semicolon-separated, with a Hindi tag field |
| `garbage.bin` | Binary noise, invalid UTF-8, an oversize line (200 KB), an empty line, a 1-byte line |

- [x] 20. `contracts-repo/` seeded (C): git-init it. Add placeholder library contracts `linux_sshd.yaml` and `acme_ngfw_cef.yaml` in IF-CONTRACT-YAML format; C2/C3 finalize them.

### S0.5 LLM host setup (Person C)
- [x] 21. Install Ollama natively and pull the laptop model.
  - Run `ollama ps` during a request and confirm the model is **100% GPU**.
  - Send one structured-output request (JSON schema) and confirm it returns valid JSON.
  - Record VRAM usage.

### S0.6 Pin and publish
- [x] 22. Fill **IF-VERSIONS** in `02_CONTRACTS.md` with the exact versions and tags in use. Add a `VERSION-PIN` changelog entry. <!-- Node pinned at 25.2.1, not an LTS line: decision D17 -->
- [x] 23. Everyone runs the acceptance checks on the **demo laptop**.

## Acceptance criteria

- [x] AC1: `make up PROFILE=laptop` brings every container to healthy within 3 minutes. `docker stats` total is ≤ 9 GB (no Ollama, idle).
- [x] AC2: `make topics` creates every IF-TOPICS topic with profile partitions. `kafka-topics --describe` output is saved in the report.
- [x] AC3: `fake_raw.py --eps 50` for 60 s; a throwaway consumer counts 3000 valid envelopes. `veyra_common` model validation passes on all of them.
- [x] AC4: The Wazuh dashboard opens at `https://localhost:8443`. A manual NDJSON line appended to `data/sinks/wazuh/veyra.ndjson` appears in Discover with the temporary rule 100100 (A pre-creates a minimal `veyra_rules.xml`). <!-- automated half verified via the indexer; the Discover look is still owed by a human -->
- [x] AC5: The Ollama JSON-schema request succeeds from **inside a container**, via `host.docker.internal`.
- [x] AC6: `make test` and `make lint` are green. The `template_sig` vectors pass.
- [x] AC7: `make plan-check` runs and reports zero stale files.

## Risks and fallbacks

| Risk | Fallback |
|---|---|
| Wazuh cert generation friction | Follow the official single-node guide exactly; timebox it; one person owns it |
| Kafka KRaft single-node config | Use the official image's documented env for a combined broker/controller |
| `host-gateway` not resolving | Use the docker bridge IP (`172.17.0.1`) in `.env.local` |

## Implementation notes

Executed 2026-09-26/27 by **Person A alone** (S0 is jointly owned, but nothing else can start until
it passes). Full detail, per-item ownership and evidence: `reports/S0.md`. All seven ACs pass; the
stack idles at **2.89 GiB** against a 9 GiB budget.

What the plan did not anticipate, and now must be known:

1. **`make wazuh-init` is a required one-off step** after `make wazuh-certs`. The 4.14 indexer image
   generates its own `config/opensearch.yml` from the environment and reads certificates from
   `config/certs/` with fixed names (`indexer.pem`, `indexer-key.pem`, `root-ca.pem`, `admin*.pem`);
   a custom `opensearch.yml` mounted elsewhere is silently ignored. Until the security index is
   uploaded with our CA, the indexer answers "OpenSearch Security not initialized" and the dashboard
   serves 503. Wazuh credentials are therefore the **image defaults** (admin/admin, kibanaserver);
   rotating them with `wazuh-passwords-tool` is an S2 item.
2. **Rule 100100 must be a child of built-in rule 99000** (`<if_sid>99000</if_sid>`). Wazuh ships 99000
   "Amazon Security Lake rules grouped" at level 0, matching any JSON event with `activity_id` and
   `category_uid` — every OCSF event, so every VEYRA event. A sibling rule loses to it and, because
   99000 is level 0, no alert is produced at all. This changes no payload; A6 hangs 100110-100130
   under 100100.
3. **Anything a container writes as its own uid needs a named volume, not a host bind mount.** Empty
   bind mounts wiped the manager's `/etc/filebeat` and `/var/ossec`, and the indexer hit
   `AccessDeniedException` on a host-owned data dir. immudb needed `USER` set plus the invoking uid
   (`VEYRA_UID`/`VEYRA_GID`, written by `make`) to keep `./data` free of root-owned files.
4. **Two host ports were already taken** on the demo laptop, so both are knobs:
   `VEYRA_IMMUDB_PG_HOST_PORT` (default 5433; 5432 inside the network) and `VEYRA_CONSOLE_PORT`
   (default 8080 per IF-PORTS). Host-side tools reach Kafka on its EXTERNAL listener via
   `VEYRA_KAFKA_BOOTSTRAP_HOST` (localhost:29092).
5. **ClickHouse refuses to boot** if `background_pool_size` is set below 10, because
   `background_pool_size * background_merges_mutations_concurrency_ratio` must stay at or above
   `number_of_free_entries_in_pool_to_execute_mutation` (20). Do not "save memory" there.
6. **A transactional producer needs `transaction.timeout.ms` >= `delivery.timeout.ms`**, or
   librdkafka rejects it outright (`_INVALID_ARG`). Both are knobs
   (`VEYRA_KAFKA_TXN_TIMEOUT_MS`, `VEYRA_KAFKA_DELIVERY_TIMEOUT_MS`), equal by default. Found by the
   integration test, not by inspection — which is the argument for writing those tests in S0.
7. **S0.5's "100% GPU" check FAILS on this laptop**: Ollama discovers only `library=cpu` despite the
   driver, `/dev/nvidia*`, `libcuda.so.1` and its own `cuda_v12` runner being present. Drafts take
   **53-91 s** on CPU, against the demo's 5 s fallback window, so Beat 2/Beat 4 depend on
   `LLM_MODE=cache` until it is fixed (`ACTION REQUIRED @C`). AC5 itself passes: schema-constrained
   JSON works from inside a container via `host.docker.internal`, which needed
   `OLLAMA_HOST=0.0.0.0:11434` as a systemd drop-in.
8. **The laptop power-cut once** during image builds + Wazuh + tests running together. Keep builds and
   `make test-int` away from rehearsals and from the demo itself.
