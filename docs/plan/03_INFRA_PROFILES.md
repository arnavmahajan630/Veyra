# 03 — INFRA PROFILES

**Default: `laptop`.** Better hardware means switching the profile, not editing code (principle P5).

## 1. Profiles

| Profile | Target machine | Use |
|---|---|---|
| `laptop` (default) | 16 GB RAM, i5-13400H, RTX 3050 4 GB VRAM, Ubuntu LTS | Development + demo |
| `mac` | Apple Silicon, 32–64 GB unified memory | Demo on a procured MacBook |
| `workstation` | 64–128 GB RAM, 8–16+ cores, 12–24 GB+ VRAM NVIDIA, Ubuntu | Demo + scale bench + bigger LLM |
| `split` (modifier) | Any profile + Wazuh on a second machine | Use when RAM is tight |

Usage:
```bash
make up PROFILE=laptop                 # loads profiles/laptop.env, then .env.local overrides
make up PROFILE=workstation
make up PROFILE=laptop WAZUH=remote    # router sink → syslog_tcp to WAZUH_REMOTE_HOST
```

`profiles/<name>.env` holds every knob below. `.env.local` (gitignored) holds per-machine overrides. Load order: code defaults → profile → `.env.local` → shell env.

## 2. Knob table

All names are prefixed `VEYRA_`. Memory limits are enforced via compose `mem_limit`.

### 2.1 Memory, CPU and Kafka

| Knob | laptop | mac | workstation | Notes |
|---|---|---|---|---|
| `MEM_KAFKA` / `KAFKA_HEAP` | 1g / 512m | 2g / 1g | 6g / 4g | |
| `MEM_CLICKHOUSE` / `CH_MAX_MEMORY` | 1g / 800m | 3g / 2.5g | 12g / 10g | |
| `MEM_WAZUH_INDEXER` / `WAZUH_INDEXER_HEAP` | 1.8g / 1g | 3g / 2g | 8g / 4g | |
| `MEM_WAZUH_MANAGER` | 700m | 1g | 2g | |
| `MEM_WAZUH_DASHBOARD` | 800m | 1g | 2g | |
| `MEM_IMMUDB` | 256m | 512m | 1g | |
| `MEM_VECTOR` (each) | 128m | 256m | 512m | |
| `MEM_PY_SERVICE` (each) | 192m | 384m | 1g | |
| `NORMALIZER_REPLICAS` | 1 | 2 | 6 | Must be ≤ raw partitions |
| `ROUTER_REPLICAS` | 1 | 1 | 3 | |
| `RAW_PARTITIONS_PER_VENDOR` | 3 | 6 | 12 | v1 production: 48 total |
| `NORM_PARTITIONS` | 3 | 6 | 12 | |
| `KAFKA_REPLICATION` | 1 | 1 | 1 (3 with the `multi-broker` profile) | |

### 2.2 Vault, integrity and lineage

| Knob | laptop | mac | workstation | Notes |
|---|---|---|---|---|
| `SEGMENT_MAX_BYTES` | 2MB | 8MB | 128MB | v1 value is 128 MB |
| `SEGMENT_MAX_SECONDS` | 20 | 30 | 300 | v1 value is 5 min |
| `MERKLE_WINDOW_SECONDS` | 60 | 60 | 60 (3600 in the `prod-sim` preset) | v1 value is hourly |
| `ZSTD_LEVEL` | 3 | 6 | 6 | |
| `LINEAGE_TTL_DAYS` | 90 | 90 | 90 | |
| `KEY_PROVIDER` | local | local | openbao (profile `secure`) | |
| `VAULT_CHATTR` | 1 if capability present | 0 (not supported on macOS) | 1 | |

### 2.3 Engine and ingestion limits

| Knob | laptop | mac | workstation | Notes |
|---|---|---|---|---|
| `MAX_EVENT_BYTES` | 65536 | 65536 | 262144 | |
| `ENGINE_BUDGET_US` (per event) | 5000 | 5000 | 5000 | Exceeding it drops the event to tier 4. A4: checked at stage boundaries, and the engine warms its validators and timezones at load so the first event is not charged for one-time work |
| `PEEL_MAX_DEPTH` | 4 | 4 | 6 | |
| `POISON_MAX_RETRIES` | 3 | 3 | 3 | A4: attempts are counted **across restarts** via `data/state/<service>_inflight`, so a record that kills the process is skipped on the 3rd start with a tier 4 `engine_crash` DLQ record, not retried forever |
| `GATEWAY_DEFAULT_QUOTA_EPS` | 500 | 2000 | 20000 | A2: used only when a key carries no `quota_eps`. The bucket holds one second of it, so a source may burst that many at once and then keeps to the rate; over it is 429 + `Retry-After` |
| `GATEWAY_ACK_TIMEOUT_MS` | 5000 | 5000 | 5000 | A2: how long the gateway waits for Kafka before answering **503**. It never answers 200 for anything unacknowledged, so raising this trades client latency for fewer retries |
| `GATEWAY_MAX_BODY_BYTES` | 10485760 | 10485760 | 10485760 | A2: over it is 413. Checked on `Content-Length` *and* on bytes read, since a chunked body declares no length |

### 2.4 Demo, LLM and drift

| Knob | laptop | mac | workstation | Notes |
|---|---|---|---|---|
| `DEMO_EPS_BASELINE` | 15 | 30 | 200 | Background traffic during the demo |
| `BENCH_TARGET_EPS` | 2000 | 5000 | 30000+ | A6 bench |
| `LLM_MODEL` | `qwen2.5:3b` (Q4) or C4 bench winner ≤ 3.5 GB VRAM | 7B–14B instruct (Q4/Q5) | 14B–32B instruct (fits VRAM) | See §4 |
| `LLM_NUM_CTX` | 4096 | 8192 | 16384 | |
| `LLM_TIMEOUT_S` | 25 | 20 | 15 | |
| `LLM_MODE` | live_then_cache | live_then_cache | live | |
| `OLLAMA_URL` | `http://host.docker.internal:11434` (host-gateway) | same (native Ollama, Metal) | same, or the in-compose `ollama` with GPU | |
| `DRIFT_MIN_CLUSTER` | 5 | 5 | 20 | |
| `SSE_TICK_MS` | 1000 | 1000 | 500 | |

### 2.5 Control plane, registry and drift (C1–C3) <!-- synced from C1/C2/C3 -->

These are the same on every profile today; none is hardware-bound yet. They live in `veyra_common.settings.Settings` and all three `profiles/*.env`.

| Knob | Value | Notes |
|---|---|---|
| `CONTROL_API_PORT` | 8000 | IF-PORTS |
| `CONTROL_DB` | `data/control/control.db` | SQLite, WAL |
| `CONTRACTS_REPO` | `../contracts-repo` | The separate registry repository beside `Veyra/` (the container mounts it at `/contracts-repo`) |
| `INVENTORY_FILE`, `INVENTORY_RELOAD_STAMP` | `edge/vector/inventory/sources.csv`, `edge/vector/reload.stamp` | IF-INVENTORY |
| `SESSION_TTL_MIN` | 480 | |
| `DEMO_PASSWORD`, `PUBLIC_HOST` | `veyra-demo`, `localhost` | |
| `CONTROL_PUBLISH_TIMEOUT_S` | 5 | A `control` flush longer than this answers 503 and rolls back |
| `SSE_HEARTBEAT_S`, `SSE_QUEUE_MAX` | 15, 256 | |
| `API_PAGE_DEFAULT`, `API_PAGE_MAX` | 200, 1000 | |
| `BACKTEST_MAX` | 200 | Events per backtest |
| `REPLAY_MAX`, `REPLAY_TIMEOUT_S`, `REPLAY_POLL_MS` | 10000, 60, 500 | |
| `EVIDENCE_API_URL`, `EVIDENCE_TIMEOUT_S` | `http://evidence-api:8100`, 5 | At the paths Caddy forwards |
| `DRIFT_DEBOUNCE_MS`, `DRIFT_MAX_SAMPLES` | 2000, 5 | |
| `DRIFT_DRAIN_SIM_TH`, `DRIFT_DRAIN_DEPTH` | 0.4, 4 | Drain3 |
| `DRIFT_CHECKPOINT_MS`, `DRIFT_POLL_MS` | 5000, 500 | Offsets are committed after each checkpoint |
| `DRIFT_WORKER_PORT`, `DRIFT_WORKER_URL`, `CONTROL_API_URL` | 8206, `http://drift-worker:8206`, `http://control-api:8000` | |
| `LIBRARY_MATCH_MIN` | 0.8 | Tier-1 share for a library pack to count as a match |

## 3. Memory budget (laptop)

| Component | Limit |
|---|---|
| OS + browser + screen recorder | ~3.0 GB |
| Wazuh (indexer 1.8 + manager 0.7 + dashboard 0.8) | 3.3 GB |
| Kafka | 1.0 GB |
| ClickHouse | 1.0 GB |
| immudb + 2× Vector | 0.5 GB |
| 9 Python services × 192 MB | 1.7 GB |
| Caddy | 0.05 GB |
| Ollama host process (model in VRAM) | ~0.5 GB |
| **Total** | **~11 GB (≈ 5 GB headroom)** |

If headroom drops under 2 GB during rehearsal, switch to `WAZUH=remote` (Wazuh on a teammate's laptop over LAN). That frees 3.3 GB.

Laptop-specific setup:
- Enable swap of at least 8 GB (`swapfile`) as a safety net; the demo must never actually swap.
- Close the IDE before the demo.

## 4. LLM tiers

The drafter only has to do one thing: given a template, its tokens and a small field catalogue, return a JSON mapping. That is well within small-model ability, because the output is constrained by the JSON schema and by token references (IF-LLM-DRAFT).

| Hardware | Model class | Notes |
|---|---|---|
| RTX 3050 4 GB | 3–4B instruct, Q4_K_M | Keep it fully on GPU. `OLLAMA_KEEP_ALIVE=30m`; pre-warm with `make llm-warm`. |
| Mac 32 GB+ | 7B–14B instruct | Native Ollama (Metal). Docker on macOS has no GPU access, so never run Ollama in compose on a Mac. |
| Workstation 12–24 GB VRAM | 14B–32B instruct | Can run in compose with the NVIDIA runtime or natively. |

**Choose by measurement, not by name.** C4 ships `make bench-llm`, which runs the golden template set (20 templates with expected mappings) against `LLM_MODEL` and reports:
- field accuracy;
- provenance pass rate;
- p50/p95 latency.

Record results in `reports/C4-bench-<machine>.md` and set the winner in the profile. Newer or better small models released by build time should simply be benchmarked and swapped in.

**Fallbacks (always available):**
- `LLM_MODE=cache` replays recorded drafts keyed by `template_sig`.
- `LLM_MODE=heuristic` uses rule-based drafting.

The demo never depends on the LLM responding live.

## 5. Platform notes

**Ubuntu laptop (default):**
- Docker Engine + compose plugin.
- Ollama installed natively (its installer handles the NVIDIA driver path).
- Compose services reach it via `extra_hosts: host.docker.internal:host-gateway`.
- The archiver container needs `cap_add: [LINUX_IMMUTABLE]` for `chattr +i`. If unavailable, set `VAULT_CHATTR=0`; 0444 mode still applies.

**macOS:**
- Docker Desktop, with its memory slider set to ≥ 16 GB.
- **Check arm64 images at S0.** Kafka, ClickHouse, immudb, Vector and Caddy publish multi-arch images. If the pinned Wazuh images lack arm64, run Wazuh with `WAZUH=remote` on a Linux machine, or accept emulation only if rehearsal shows it is stable.
- `chattr` is unavailable; use `VAULT_CHATTR=0`.

**Workstation:**
- Enable `docker-compose.obs.yml` (Prometheus + Grafana) and `docker-compose.secure.yml` (OpenBao).
- Run `make bench-throughput` for the scaling slide.

## 6. Adding or tuning a profile

1. Copy `profiles/laptop.env` to `profiles/<name>.env` and change the values.
2. `make up PROFILE=<name> && make e2e-smoke && make bench-throughput && make bench-llm`.
3. Commit the profile plus a short `reports/profile-<name>.md` with `docker stats` and bench numbers.

Never put machine-specific values in code or compose files. Compose reads `${VEYRA_*}` everywhere.
