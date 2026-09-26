# 00 — MASTER: VEYRA Demo

> Every agent session starts by reading this file in full. It is the context an agent cannot infer from code.
> Contract version this file assumes: **contracts v1.0** (see `02_CONTRACTS.md`).

---

## 1. What we are building

**VEYRA** is an air-gapped log pre-processing framework for SIH problem statement **SIH26156 (NTRO — Universal Log Pre-processing Framework)**.

It receives heterogeneous, messy security logs and does four things:
- preserves the exact original bytes as tamper-evident evidence;
- normalizes the logs into **OCSF**, using a lineage extension called **ulpf**;
- routes them to existing SIEMs;
- shows operators and onboarded organisations what happened to every event.

VEYRA is **a pre-processor, not a SIEM**. It never does detections or threat analytics; the SIEM does.

The full production design is in `VEYRA_System_Architecture_Document.docx`, referred to as **v1** in this plan (sections cited as `v1 §N`). This project builds a **running, faithful-lite version of v1** on one machine, plus a 3-minute live demo.

**One-line pitch:** *"Any log, however messy, is sealed as evidence the moment it arrives, normalized deterministically, delivered to your SIEM immediately, and traceable byte-for-byte back to its source. Formats it has never seen become approved parsers in minutes, not weeks."*

## 2. What the judges must see (problem-statement mapping)

| PS point | What the demo proves | Where in demo (`04_DEMO_SCRIPT.md`) |
|---|---|---|
| (d) Traceability between normalized and original events | Click any normalized field and its source bytes highlight. One-click cryptographic verify. A tamper attempt is detected and located. | Beat 5 |
| (e) Plug-and-play onboarding of new sources | Org onboards a source from 3 pasted samples. An unseen format becomes an approved contract via drift → draft → approve → replay. | Beats 2 and 4 |
| (f) Unified visibility across enterprise environments | One console shows every source's health, parse quality (tiers), delivery status and evidence status, across zones. | Beats 3–5 |
| Core normalization | Ultra-messy logs are normalized and delivered to **Wazuh**; Wazuh alerts fire on the normalized fields. | Beats 3 and 4 |

## 3. Demo north star

The demo is a 3-minute live run on one machine, fully offline. Every beat is reproducible from a reset.

1. **Hook** (0:00–0:15).
2. **Onboard an org source** in the console (0:15–0:40).
3. **Log storm**: clean, CEF and ultra-messy logs arrive; tier counters move; Wazuh receives everything; a brute-force alert fires on a clean source; the messy source arrives as Tier 3 (0:40–1:25).
4. **Drift loop**: new template detected → LLM draft with provenance highlights → approve → shadow diff → promote → replay → Wazuh events upgrade and a brute-force alert fires *retroactively* for the messy source (1:25–2:10).
5. **Traceability**: field-to-bytes highlight → verify green → insider tamper → verify red, with the break located (2:10–2:50).
6. **Architecture close** (2:50–3:00).

Details, narration and fallbacks are in `04_DEMO_SCRIPT.md`.

## 4. Non-negotiable principles

Every agent must uphold these. A change that violates one is a bug, even if tests pass.

| ID | Principle | Concretely |
|---|---|---|
| P1 | **Evidence before parsing** (v1 ADR-02) | The raw envelope (`event_uid`, `raw_sha256`, raw bytes) is created at the edge/gateway before any parsing. Nothing downstream mutates raw bytes. |
| P2 | **Never drop** (v1 §9.1) | Every event reaches the SIEM route at some tier (1–4). Rejection is not an outcome. Tier 2–4 also go to the DLQ. |
| P3 | **Deterministic hot path** (v1 ADR-04) | The normalizer never calls an LLM. Same input + same contract version = byte-identical output. The LLM only drafts contracts in the control plane. |
| P4 | **Every field traceable** | Every mapped OCSF value carries a byte offset into the raw event (`ulpf.field_offsets`) or is explicitly marked as derived or constant. |
| P5 | **Profile-driven scale** | No hard-coded limits, partitions, model names, windows or intervals. Everything comes from env and profile (`03_INFRA_PROFILES.md`). |
| P6 | **Demo determinism** | A scripted scenario plus a reset command reproduces the demo identically. The LLM has a recorded-response fallback. |
| P7 | **Real where visible** | Kafka, Vector, ClickHouse, immudb, Wazuh and Ollama are real. What the judge sees working must actually work. Slide-only items are declared (§6). |
| P8 | **Honest deviations** | Every place the demo differs from v1 is listed in §7 and said aloud if asked. |

## 5. Architecture (demo build)

```
                     ┌──────────────────────── CONTROL PLANE (Track C) ─────────────────────────┐
                     │ control-api (FastAPI+SQLite+git contracts)  drift-worker (Drain3)        │
                     │ llm-drafter (Ollama, token-ref drafting, provenance check)               │
                     │ publishes: contracts, api-key registry, source inventory → topic control │
                     └──────────────┬───────────────────────────────────────────▲───────────────┘
                                    │ control topic (compacted)                 │ dlq, shadow, lineage
  SOURCES (demo generators)         ▼                                           │
  syslog UDP/TCP ──► edge-dmz / edge-core (Vector)  ─┐                          │
  HTTP push (API key) ─► ingest-gateway (FastAPI) ───┼─► KAFKA raw.<vendor> ─┬─► normalizer (Track A) ─► norm.<category>, lineage, dlq, shadow
  batch upload ────────► ingest-gateway ─────────────┘   (stamped envelopes) ├─► archiver (Track B) ─► vault segments (WORM) ─► vault_index
                                                                             └─► lineage-indexer (B) ─► ClickHouse
                                                         norm.* ─► router (A) ─► Wazuh (NDJSON sink) + partner file (masked) ─► receipts
                                                         vault_index ─► integrity (B) ─► Merkle root/window, Ed25519 sign ─► immudb + ledger
  CONSOLE (React) ◄─ caddy ─► /api/control → control-api   /api/lineage, /api/evidence → evidence-api (B)   /api/demo → demo-engine (B)
```

### 5.1 v1 layer → demo mapping

| v1 layer | v1 component | Demo build | Status | Owner |
|---|---|---|---|---|
| L1 Zone Edge | Vector collectors, active/passive, keepalived | Vector per zone (dmz, core), socket sources, VRL stamping, enrichment-table source resolution, disk buffer. No keepalived. | Built (lite) | A |
| L1 (new) | Push ingest with per-source key | `ingest-gateway`: HEC-compatible + batch, API key → source, quotas, custody flag | Built | A |
| L2 Kafka | 3 brokers RF3, KRaft | 1 broker KRaft. Same topic names, keys, transactions, idempotence. Partitions from profile. | Built (lite) | S0/A |
| L3 Evidence Vault | Object-locked storage, seekable zstd, per-segment keys, OpenBao/HSM | Segments on disk (zstd, AES-GCM per-segment key wrapped by KeyProvider), read-only + optional `chattr +i` | Built (lite) | B |
| L3 Integrity | Partition hash chain, hourly Merkle, HSM sign, WORM export | Same chain formula; Merkle per window (60 s laptop, 3600 s prod); Ed25519 via KeyProvider; roots chained and stored in **immudb** + ledger file | Built | B |
| L4 Normalization | Stateless Vector/VRL, Source Packs | Python `veyra_engine`: contracts compiled to deterministic parse plans, Tier 1–4, peeling, field offsets, Kafka transactions | Built (deviation D4) | A |
| L5 Router | Declarative routes, formats, PII masking, receipts | `router`: routes.yaml, OCSF JSON to Wazuh NDJSON sink, masked partner route, receipts | Built | A |
| L5 Lake | Iceberg + Nessie + Trino | Not built. Slide only. | Slide | — |
| L5 Lineage index | ClickHouse + Keeper | ClickHouse single node; tables + materialized views; TTL | Built | B |
| L6 Control plane | Gitea, Pack CI, Studio, Drain3, local LLM, canary/shadow, four-eyes | control-api + SQLite + local git repo; golden tests; Drain3 worker; Ollama drafter; backtest + live shadow; four-eyes enforced | Built | C |
| L7 Console | Source Health, Lineage Explorer, Evidence Export, Delivery Monitor, Compliance, Audit | React console: Overview, Onboarding, Sources, Contracts & Drift, Lineage, Evidence, Delivery, Audit, Demo panel. EN/HI toggle. | Built (Compliance = stretch) | C + B |
| L8 Platform | RKE2, Keycloak, OpenBao, HSM, Harbor, Prometheus/Grafana/Loki, NTP | Docker Compose appliance mode (v1 §12.1). Local roles. KeyProvider(local \| openbao). Optional observability profile. | Lite / Slide | S0 |

## 6. Component catalogue

| Service | Owner | Lang | Port (host) | Consumes | Produces |
|---|---|---|---|---|---|
| `kafka` | S0 | — | 9092 | — | — |
| `edge-dmz`, `edge-core` | A | Vector config + VRL | 5514/udp, 5515/tcp (dmz); 5524/udp, 5525/tcp (core) | syslog | `raw.<vendor>` |
| `ingest-gateway` | A | Python | 8088 | HTTP push, `control` (keys, sources) | `raw.<vendor>` |
| `normalizer` | A | Python (`veyra_engine`) | 8201 (metrics) | `raw.*`, `replay.raw`, `control` | `norm.*`, `lineage`, `dlq`, `shadow` |
| `router` | A | Python | 8202 (metrics) | `norm.*` | Wazuh NDJSON, partner NDJSON, `receipts` |
| `wazuh` (indexer, manager, dashboard) | A | config + rules | 8443 (dashboard) | NDJSON sink | alerts |
| `archiver` | B | Python (`veyra_evidence`) | 8203 | `raw.*` | segments, `vault_index` |
| `integrity` | B | Python | 8204 | `vault_index` | signed roots → immudb, ledger |
| `lineage-indexer` | B | Python | 8205 | `raw.*`, `norm.*`, `lineage`, `vault_index`, `receipts`, `dlq`, `shadow`, `audit` | ClickHouse |
| `evidence-api` | B | Python FastAPI | 8100 | ClickHouse, vault, immudb | lineage/evidence REST |
| `demo-engine` | B | Python FastAPI | 8300 | scenario files | syslog/HTTP traffic, resets, tamper |
| `control-api` | C | Python FastAPI | 8000 | SQLite, git, ClickHouse (read) | `control`, `replay.raw` requests |
| `drift-worker` | C | Python | 8206 | `dlq` | drift items → control-api |
| `llm-drafter` (library inside control-api) | C | Python | — | Ollama | drafts |
| `console` | C (shell + most pages), B (lineage, evidence, demo pages) | TypeScript React | via caddy 8080 | APIs | UI |
| `caddy` | S0 | config | 8080 | — | reverse proxy + static console |
| `clickhouse` | B | — | 8123 | — | — |
| `immudb` | B | — | 3322, 5432 (pg wire) | — | — |
| `ollama` | C | — | 11434 (native install recommended) | — | — |
| `openbao` | B | — | 8200 (profile `secure`, optional) | — | — |

## 7. Declared deviations from v1

Say these honestly if a judge asks.

1. **Normalizer runs in Python, not VRL (D4).** Contracts compile to a deterministic parse plan. Production target: compile to VRL. Same guarantees: deterministic, versioned, no runtime LLM.
2. **JSON envelope on the wire, not Protobuf.** Raw bytes are base64 inside JSON. A `.proto` is defined in contracts for production.
3. **Single Kafka broker.** Durability settings are kept (acks=all, idempotence, transactions); RF3 is a profile change.
4. **Merkle window is 60 s**, not hourly, so roots appear during a 3-minute demo. Configurable.
5. **Vault is a local filesystem with read-only and immutable flags**, not S3 Object Lock. Production target: Ceph RGW Object Lock (COMPLIANCE). The segment format is storage-agnostic.
6. **Local key provider by default.** OpenBao transit is an optional profile. HSM is slide only.
7. **No Iceberg lake, no Keycloak, no RKE2, no Harbor.** Slide only.
8. **Tier 2/3 fallback and template signatures** go beyond v1 (v1 only raw-wraps). These are additions that fill v1 gaps, not deviations.
9. **Multi-tenancy is lite:** tenant ID on every record, tenant-scoped console views, per-source keys. There is no per-tenant topic or storage isolation.
10. **Demo engine mounts the Docker socket** (demo profile only), to restart services during reset. It is never present in a production profile.

## 8. Decision log

| ID | Decision | Why | Revisit if |
|---|---|---|---|
| D1 | Python 3.12 for all services, `uv` for deps, ruff + pytest | Drain3 and ML tooling; agent reliability; team familiarity | — |
| D2 | React + Vite + TypeScript + Tailwind for console, built to static and served by Caddy | Fast polished UI via agents; no Node at runtime | — |
| D3 | Vector (config only) for syslog edge; Python gateway for HTTP push | Faithful to v1; key auth is easier in Python | Vector lacks a needed VRL function → see A1 fallback |
| D4 | Contracts compile to a Python parse plan in `veyra_engine`, not VRL | Tier 3, peeling and offsets are far easier; still deterministic | — |
| D5 | immudb for the signed-root ledger (pg wire) | Verifiable append-only store with one container; Python via `psycopg` | pg-wire issues → immudb Python SDK or ledger-file only |
| D6 | Wazuh ingests via shared-volume NDJSON file with `log_format json` | Most reliable single-node path | Remote Wazuh profile → syslog TCP sink |
| D7 | Merkle window and segment seal are profile knobs (laptop: seal 20 s / 2 MB, window 60 s) | Visible within the demo | — |
| D8 | KeyProvider interface: `local` (default) \| `openbao` | Keeps the demo light and the upgrade path real | — |
| D9 | Control plane state in SQLite; contracts versioned in a local git repo | Zero extra services | — |
| D10 | LLM drafts by choosing **token references**, never free text values | Provenance is guaranteed by construction; hallucinated values are impossible | — |
| D11 | Deterministic `template_sig` at runtime; Drain3 only offline in drift-worker | Keeps the hot path deterministic (P3) | — |
| D12 | Correction semantics for Wazuh: re-emit with `ulpf.revision+1` and `ulpf.supersedes` | Wazuh can't upsert | — |
| D13 | Shadow = instant **backtest** on stored DLQ samples + live shadow counts | Instant, visible results in the demo | — |
| D14 | Console live data via SSE from the APIs (1 s tick) | Simple, reliable | — |
| D15 | Ollama runs natively on the host by default, not in Docker | GPU and Metal access without container GPU plumbing | — |
| D16 | One reverse proxy (Caddy) on :8080 for the console and all APIs | One origin, no CORS | — |

## 9. Repo layout (monorepo, created in S0)

```
veyra/
  compose/
    docker-compose.yml          base services
    docker-compose.wazuh.yml    Wazuh single-node (separate file so it can run remotely)
    docker-compose.secure.yml   OpenBao (optional)
    docker-compose.obs.yml      Prometheus/Grafana (optional)
  profiles/                     laptop.env, mac.env, workstation.env (see 03)
  packages/
    veyra_common/               settings, envelope models, topics, kafka helpers, ids, logging  (S0, then shared)
    veyra_engine/               parse/normalize engine library                                  (A)
    veyra_evidence/             chain, segment, merkle, keyprovider, verify                      (B)
    veyra_lineage/              ClickHouse schema + query functions                              (B)
    veyra_contracts/            contract YAML models, compiler, golden test runner               (C, uses veyra_engine)
  services/
    ingest_gateway/ normalizer/ router/                                                          (A)
    archiver/ integrity/ lineage_indexer/ evidence_api/ demo_engine/                             (B)
    control_api/ drift_worker/                                                                   (C)
  edge/vector/                  vector-dmz.toml, vector-core.toml, VRL, inventory CSV            (A)
  wazuh/                        ossec.conf fragments, rules/veyra_rules.xml                      (A)
  console/                      React app                                                        (C shell; B pages)
  contracts-repo/               git repo of Log Contracts (runtime data; seeded)                 (C)
  demo/                         scenarios/, corpus/, expected/                                   (B; corpus in S0)
  data/                         runtime volumes (gitignored)
  docs/plan -> this folder      (copy this plan folder into the repo as docs/plan)
  Makefile                      up, down, reset, seed, test, demo-*, bench-*
```

## 10. Quality bar

"Sophisticated" means all of the following. Reviewers reject phases that miss them.

- **Typed and validated:** Pydantic models for every record in `02_CONTRACTS.md`. Every consumer validates input and routes invalid records to the DLQ with a reason; nothing crashes on bad input.
- **Tested:**
  - unit tests for every engine and evidence function;
  - test vectors from `reference/spec_vectors.py` pass;
  - golden tests for every seeded contract;
  - one end-to-end smoke test per checkpoint (`make e2e`).
- **Observable:** every service has `/healthz` and `/metrics` (Prometheus text) and logs structured JSON with `event_uid` where relevant.
- **Restart-safe:** killing any service and restarting it loses nothing and double-processes nothing inside the core (Kafka transactions, offsets committed with output).
- **Profile-driven:** zero magic numbers. All knobs in `veyra_common.settings` with defaults from the profile.
- **Demo-grade UX:** the console loads in under 1 s, is live (SSE), shows no empty states during the demo, and every destructive action confirms.
- **Resettable:** `make demo-reset` returns to the seeded pre-demo state in under 90 s on the laptop profile.

## 11. Non-goals

- Security analytics, detections or correlation (that's Wazuh's job).
- Real HA, multi-broker or multi-node.
- Real HSM, Keycloak SSO or TLS everywhere (mTLS is optional in the `secure` profile).
- Handling 60k EPS on the laptop. The claim is architectural. The workstation profile bench (A6) shows the scaling curve.

## 12. Open questions (defaults in force until answered)

| Q | Default |
|---|---|
| Demo org name | "Maha Power Corp" (tenant `t_maha_power`) plus pre-seeded "NTRO Core Ops" (`t_ntro_core`) |
| Live demo or recorded? | Live, with a recorded backup video (S2) |
| OCSF version | Pinned at S0 (latest stable 1.x); stored in `IF-VERSIONS` |
| Hindi toggle in console | Yes, on the key labels only (v1 §3.1 lists English + Hindi) |
| Second SIEM | No. Second route is a masked "partner" NDJSON file to show route-local PII masking |

## 13. Glossary

| Term | Meaning |
|---|---|
| Envelope | Stamped raw record on `raw.*` (`IF-ENVELOPE`) |
| Contract | A **Log Contract** (v1's Source Pack): declarative parsing and mapping for a source (`IF-CONTRACT-YAML`) |
| Template / `template_sig` | A message shape inside a source; the signature is deterministic (`IF-TEMPLATE-SIG`) |
| Tier | Parse outcome 1–4: match, partial, unknown_template, unparseable |
| Conformance | The string form of the tier, carried in `ulpf.conformance` |
| Drift item | A new or unknown template cluster the control plane surfaced for review |
| Draft | A proposed contract change, from the LLM or the heuristic drafter |
| Backtest | Running a candidate contract over stored samples instantly |
| Shadow | A candidate contract running alongside the active one on live traffic |
| Revision | Re-emission of the same `event_uid` after replay with a newer contract |
| Segment | Sealed vault file of raw records for one topic-partition |
| Window root | Merkle root over segments sealed in a time window, signed and chained |

## 14. How this plan evolves

Read `01_TEAM_GUIDE.md` §6. In short: phase done → report in `reports/` → update `06_STATUS_BOARD.md` → if anything shared changed: bump `02_CONTRACTS.md`, log in `05_CHANGELOG.md`, patch every affected plan file (grep the `IF-*` IDs).
