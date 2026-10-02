# Veyra demo guide: steps, scope and how deep it goes

As of 30 September 2026. This guide assumes you ran `./veyra.sh up` (`.\veyra.ps1` on Windows)
and it ended with **Ready**. It covers:

1. what setup did, stage by stage;
2. the guided demo, beat by beat, with what each beat proves;
3. a scope matrix: what is built, what is a prototype, what is not built yet;
4. how to go deeper by hand;
5. load testing and how to read the numbers;
6. known limits.

---

## 1. What `up` does

| # | Stage | What happens | First run | Later |
|---|---|---|---|---|
| 1 | Checking this machine | Docker, Compose v2, git; RAM, CPUs and disk as Docker sees them; picks the profile; probes for an NVIDIA GPU | < 1 min | seconds |
| 2 | Contract registry | Clones `../contracts-repo` if it's missing | < 1 min | skipped |
| 3 | Settings and data folders | Writes `.env.runtime` = profile + `.env.local` + script overrides; creates `data/` | seconds | seconds |
| 4 | Build | The Veyra Python image, the React console (in a Node 25 container), the edge configs, the evidence keys | 5–10 min | seconds (cached) |
| 5 | Infrastructure | Kafka, ClickHouse, immudb, the two Vector edges, Caddy, the Kafka UI; waits until healthy; creates every topic | 2–4 min | < 1 min |
| 6 | Wazuh (workstation) | Sets `vm.max_map_count`, makes certificates once, starts the indexer, manager and dashboard, initialises security once | 3–6 min | ~1 min |
| 7 | AI drafter model | Starts the chosen model server (GPU if found), downloads its model, loads it. `--drafter ollama` (default): Ollama and the profile's LLM. `--drafter laya`: Ollaya and the Laya decision model. Stage 1 asks which on the first run | 2–10 min (download; under 2 min for Laya) | seconds |
| 8 | Veyra services | Gateway, normalizer, control API, drift worker, lineage indexer, archiver, integrity, evidence API; waits until each answers | 1–2 min | < 1 min |
| 9 | Smoke check | Every service answers; all topics exist; admin can sign in; a syslog probe comes out as OCSF tier 1 and reaches the Wazuh sink file | < 1 min | < 1 min |
| 10 | Ready | Prints URLs and sign-ins | — | — |

Script overrides the demo relies on (in `.env.runtime`, never in your `.env.local`):
- `VEYRA_SEGMENT_MAX_SECONDS=20`, so evidence seals within the demo;
- `VEYRA_DEMO_MODE=1`, which enables the demo user switch and last-key endpoint;
- `VEYRA_OLLAMA_URL=http://ollama:11434`, with the `ollama` drafter;
- `VEYRA_LLM_BACKEND=decision`, `VEYRA_DECISION_URL=http://ollaya:11435`, `VEYRA_DECISION_MODEL` and `VEYRA_LLM_MODE=live_then_cache`, with the `laya` drafter;
- `VEYRA_LLM_MODE=cache`, only when no model is available.

---

## 2. The guided demo: `./veyra.sh demo`

Open the console (http://localhost:8080) and the Kafka UI (http://localhost:8085) side by side and
watch the topics fill as the beats run. The walkthrough pauses after each beat; use `--auto` to run
straight through, or `--beat N` to repeat one beat.

| Beat | What it does | What it proves | Problem-statement point |
|---|---|---|---|
| **1. Ingest over syslog** | Sends the Linux sshd corpus over real syslog (UDP to the core edge). Sends the vendor firewall's CEF events stamped exactly as the DMZ edge would, straight onto `raw.acme_ngfw` (see Known limits). Shows the normalized counts by source and tier, and one sshd event with its contract, Kafka coordinates and byte offsets. Sshd shapes the seeded contract doesn't cover yet arrive as tier 3 | Devices send ordinary syslog; each line is stamped (id, time, SHA-256) before parsing, queued, and translated to OCSF at tier 1 | Core normalization |
| **2. Messy and garbage logs** | Sends the OT historian (Hindi field names, no registered source) and binary garbage | Nothing is dropped. The unknown shape is **tier 3**, with IPs and users pulled out and their byte offsets, plus a category hint that never overrides the real class. Garbage is **tier 4** and still delivered | Core normalization; P2 "never drop" |
| **3. Onboard a new source** | As `author@maha`: registers `src_authsrv_01`, pastes T1/T2 samples into onboarding, gets a draft contract, submits it. The author's own approval is **refused (HTTP 403)**; `approver@veyra` approves and promotes. Then issues a per-source API key and pushes T1/T2 over the HEC endpoint | Plug-and-play onboarding with four-eyes; push ingest with revocable per-source keys; the new contract is live on the normalizer within seconds | (e) plug-and-play onboarding |
| **4. Drift → draft → replay** | Pushes 8 T3 "FAILED login" events (multi-line, with stack traces). They arrive as **tier 3**, so a brute-force rule can't see them. The drift worker raises a drift item; a draft is made (rules drafter by default, `--draft-mode live` for the AI); it's submitted, approved by the second person, promoted; then the 8 stored events are **replayed as revision 2 at tier 1** | Veyra learns an unseen format safely: the AI can only point at real tokens, checks run, a human approves, and history is corrected after the fact | (e) onboarding; retroactive detection |
| **5. Evidence** | Takes an archived sshd event and waits for its segment to seal and its minute's root to be signed. Runs **verify** (7 of 8 steps pass). An insider with root and the encryption key rewrites the stored bytes: verify turns **red at `merkle_inclusion`**. Untamper → green again. Audits the whole signed ledger | Stored bytes are provably untouched; an edit is detected and located; the ledger is chained and signed | (d) traceability |

The walkthrough prints PASS/FAIL per step and a tally at the end. A failing step doesn't stop the
demo: it says why and moves on.

**Talking points while it runs:**
- "Evidence before parsing": the SHA-256 is taken at the edge, before any service reads the log.
- "Never drop": watch tier 3 and tier 4 arrive in `norm.*`; nothing is rejected.
- "The AI can't make things up": a draft refers to numbered tokens of the real log, never to free text.
- "Four-eyes": the 403 in beat 3 is the author trying to approve their own change.
- "A pre-processor, not a SIEM" (workstation, Wazuh on): open the Wazuh dashboard (https://localhost:8443), Discover, and filter on `rule.id` 100100 to 100130. Beat 1's sshd failures show as 100110 and the 100111 brute-force alert; beat 4's replayed events show as 100130, "corrected event, revision 2". The detection is Wazuh's; Veyra only fed it.

---

## 3. Scope: how deep each capability goes

**Built** = runs in the one-command stack. **Prototype** = runs, with a limit that's listed. **Not built** = planned; it's in the design docs but not running.

| Capability | Status | Depth today | How to show it |
|---|---|---|---|
| Syslog ingest (Vector edges, two zones) | **Built** (A1) | UDP/TCP, per-zone listeners, source resolution from the inventory, multi-line joining, disk buffer (a 60 s Kafka outage loses nothing) | Beat 1; `demo/tools/send_syslog.py` |
| HTTP push (HEC-compatible gateway) | **Built** (A2) | `/services/collector/event`, `/raw`, `/v1/batch`; per-source keys, revocation within 2 s, quotas, 503 rather than silent loss | Beat 3; control API `POST /sources/{id}/keys` |
| Normalization (tiers 1–4, OCSF + `ulpf`) | **Built** (A3, A4) | Compiled Log Contracts; byte offsets for every field (including inside JSON and Devanagari text); ~2,700 events/s per core; 50,000 fuzz cases, 0 crashes | Beats 1–2; Kafka UI `norm.*` |
| Canary / shadow and replay | **Built** (A5) | A candidate version is compared on live traffic without touching output; replays supersede as revision 2, idempotent under `kill -9` | Beat 4 |
| Control plane: tenants, roles, sources, keys, audit | **Built** (C1) | 5 roles, tenant isolation, audit trail, live updates (SSE) | Console; http://localhost:8000/docs |
| Contract registry and lifecycle | **Built** (C2) | Git-versioned contracts, lint, golden tests, four-eyes, backtest, diff, rollback | Beats 3–4; console Contracts page |
| Drift detection and library packs | **Built** (C3) | Drain3 clustering of the DLQ; 5 library packs matched automatically | Beat 4; console Drift page |
| AI drafter | **Built** (C4) | Token-reference drafting with provenance, type and backtest checks; modes live / cache / rules. Answers are limited to fixed lists, so a draft can't name a value or field that isn't offered. Two backends, chosen at `up`: an Ollama LLM (1–2 s a draft on an RTX 4050; llama3.2:3b drafts the T3 shape right, qwen2.5:3b doesn't) or the Laya decision model (about 0.1 s, less accurate on which field a value fills). The checks catch a wrong draft either way, and demos default to the rules drafter | `./veyra.sh demo --draft-mode live`; `./veyra.sh up --drafter laya` |
| Web console | **Built on sample data** (C5, C6) | Overview, Sources, Onboard, Contracts, Drift, Delivery, Audit, English/Hindi. The pages use made-up data until the evidence side's lineage endpoints exist | http://localhost:8080 |
| Evidence vault (archiver) | **Prototype** (B2) | Sealed, zstd-compressed, AES-256-GCM segments; hash chain; read-only files. No crash-recovery tests; doesn't publish `vault_index` yet | Beat 5 |
| Integrity (Merkle + Ed25519) | **Prototype** (B3) | One signed root per minute, chained, in a ledger file. **Not in immudb yet** | Beat 5; `tools/ledger_audit.py` |
| Evidence API: verify and export | **Prototype** (B4) | 7 of 8 verify steps (the immudb step is not implemented); proof-pack zip with a standalone `verify.py`. No lineage search routes yet | Beat 5; http://localhost:8100/docs |
| Tamper lab | **Prototype** (B5) | 4 modes (bit flip, insider rewrite, deleted segment, edited ledger), each caught at the expected step and undoable | Beat 5; `tools/tamper.py` |
| Lineage index (ClickHouse) | **Prototype** (B1) | Every topic indexed, restart-safe. Its query speed on 1M rows isn't measured yet | `tools/seed_ch.py`; ClickHouse at http://localhost:8123 |
| Router → Wazuh, masked partner route | **Built** (A6) | Routes are data (filters, masking, formats); offsets commit only after every route flushes; receipts per route; a breaker for remote sinks. Wazuh rules: 100110 auth failure, 100111 brute force, 100120/100121 tier 3/4, 100130 corrected revision. The partner file has `user.name` and IPs HMAC'd and `raw_data` removed | Wazuh dashboard, Discover or Security events (workstation); `data/sinks/partner/partner.ndjson` |
| Demo engine, reset, hotkeys | **Not built** (B7) | `./veyra.sh demo` and `./veyra.sh reset` cover the demo for now | — |
| Console Lineage and Evidence pages | **Not built** (B6) | Use the Evidence API docs page or Beat 5 | — |
| Iceberg lake, Keycloak SSO, Kubernetes, HSM | **Slide only** | Declared deviations (`docs/plan/00_MASTER.md` §7) | — |

---

## 4. Going deeper by hand

All of these run from the repo folder once the stack is up. `docker compose` commands need
`--env-file .env.runtime -f compose/docker-compose.yml`; wrapping them in the `tools` container is
the easiest route:

```bash
T="docker compose --env-file .env.runtime -f compose/docker-compose.yml --profile tools run --rm tools"

$T python demo/tools/send_syslog.py --file linux_sshd.log --repeat 5     # more syslog traffic
$T python demo/tools/fake_raw.py --eps 50 --seconds 60                   # steady background traffic
$T python tools/tamper.py list                                           # what is tampered right now
$T python tools/tamper.py verify --event <event_uid>                     # 8-step verify from the CLI
$T python tools/ledger_audit.py                                          # re-check every signed root
$T python tools/seed_ch.py --rows 1000000                                # 1M synthetic lineage rows in ClickHouse
$T python tools/veyra_check.py                                           # the smoke check again
```

REST, via the published ports:

```bash
curl -c jar -H 'content-type: application/json' \
     -d '{"email":"admin@veyra","password":"veyra-demo"}' http://localhost:8000/auth/login
curl -b jar http://localhost:8000/sources                     # registered sources
curl -b jar http://localhost:8000/drift                       # open drift items
curl http://localhost:8100/evidence/roots                     # signed window roots
curl http://localhost:8100/evidence/<event_uid>/verify        # verify one event
curl -X POST -o proof.zip http://localhost:8100/evidence/export/<event_uid>   # proof pack
```

Push an event yourself: issue a key in the console (Sources, open a source, Keys), then:

```bash
curl http://localhost:8088/services/collector/event -H "Authorization: Splunk <secret>" \
     -d '{"event":"user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"}'
```

For development: `make test` (unit tests), `make test-int` (integration tests against the stack),
`make bench-llm MODELS=qwen2.5:3b,llama3.2:3b` (AI drafter bench).

---

## 5. Load testing: `./veyra.sh load`

There are two stages, because they answer two different questions.

**Stage A: can the bus take it?** (`--events N`, default 100 million)
- Runs Kafka's own `kafka-producer-perf-test` against a dedicated topic `bench.load`, with the same durability Veyra uses (`acks=all`, idempotent, zstd).
- Then reads the records back with `kafka-consumer-perf-test`.
- Prints records/s, MB/s, latency and elapsed time. The topic is deleted afterwards.
- It's what to run for "a billion events": `./veyra.sh load --events 1000000000 --skip-pipeline`.
- **Measured on the dev laptop** (Docker Desktop, 7 GB for Docker, 12 CPUs): 5M × 200 B produced at **~300,000 records/s** (p99 115 ms), read back at **~950,000 records/s**. At that rate 1B takes about an hour; a workstation NVMe should do better. Measure it before quoting a number.
- Disk: about `N × record size` before compression (200 GB for 1B × 200 B). The script refuses if that exceeds 70% of free space (`--force` overrides) and caps the topic's size.

**Stage B: what does Veyra sustain end to end?** (`--pipeline N`, default 1 million)
- Several producer processes push real envelopes, built from the demo corpus, into `raw.*`.
- On the workstation, 6 normalizer instances share the load; the laptop runs 1.
- Every 5 s it samples `norm.*` and prints the normalized total, the rate and the backlog.
- It ends with the sustained rate and **how long 1 billion events would take at that rate**.
- The full pipeline also archives and indexes every event, so budget about 4 KB of disk per event.
- **Measured on the dev laptop** with 1 normalizer (laptop profile, 192 MB per service): 200k envelopes produced in 5 s, then normalized at a sustained **~1,000 events/s**; peak about 1,400. The workstation profile runs 6 normalizers over 12 partitions with 1 GB each, so expect several times that; measure it with `--pipeline 5000000`.
- Either way, 1B end to end takes many hours. That's why Stage A carries the billion and Stage B carries the realistic rate.

Watch both in the Kafka UI: topic sizes, partitions, and the `normalizer` consumer group's lag
falling back to zero.

---

## 6. Known limits

- On the laptop profile Wazuh is off, so the router still writes `data/sinks/wazuh/veyra.ndjson` but nothing reads it. To ship to an existing Wazuh instead, see `wazuh/REMOTE.md`.
- Verify's 8th step (immudb) and the vault index topic are not implemented in the prototypes.
- The console runs on sample data; live panels need B4's lineage routes.
- The seeded firewall contract (`acme_ngfw_cef`) parses bare CEF. CEF that comes through the syslog edge carries a syslog header, so it lands as tier 3 until the contract gains a syslog envelope layer. The demo therefore sends CEF stamped as the edge would.
- The seeded `linux_sshd@1` covers only some of the corpus's sshd shapes; the rest are tier 3 (an owner decision is pending on adopting the fuller library pack).
- The AI drafter's small models still get drafts wrong: on the demo's T3 shape, llama3.2:3b is right, qwen2.5:3b (the laptop profile's model) and Laya are not. Checks and four-eyes catch it.
- Evidence files on a Windows drive are slower. Clone inside WSL for load tests.
- Everything else, and why: `docs/plan/00_MASTER.md` §7 (declared deviations) and `docs/plan/06_STATUS_BOARD.md`.
