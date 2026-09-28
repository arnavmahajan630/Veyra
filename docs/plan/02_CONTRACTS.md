# 02 — CONTRACTS (shared interfaces)

**Contract version: v1.4**. Bump rules are in §0. Every section has an ID (`IF-*`). Phase files reference these IDs. When you change a section, grep for its ID across the plan folder and update every file that references it.

---

## §0 Change rules

| Change type | Examples | Allowed by | Version bump | Required actions |
|---|---|---|---|---|
| **Additive** | new optional field, new endpoint, new topic, new enum value | any track owner (agent may propose, human merges) | minor (v1.0 → v1.1) | changelog entry; update this file; notify affected tracks |
| **Breaking** | rename or remove a field, change semantics, change an algorithm | agreement of all owners whose phases consume the ID | major (v1.x → v2.0) | changelog with `ACTION REQUIRED @X`; patch all plan files; migration note |
| **Clarification** | wording, examples, typo | anyone | none (note in changelog) | — |

Algorithms with test vectors (`IF-TEMPLATE-SIG`, `IF-CHAIN`, `IF-MERKLE`) are frozen after S0. Changing one is always breaking.

---

## IF-VERSIONS — pinned versions (filled by S0)

Pinned on the demo laptop (Ubuntu, i5-13400H, 16 GB, RTX 3050 4 GB) on 2026-09-26.
Do not bump a row without a `VERSION-PIN` entry in `05_CHANGELOG.md` (01_TEAM_GUIDE §4.3 rule 7).
Exact digests of the pulled images are recorded in `reports/S0.md`.

| Component | Constraint | Pinned (S0) |
|---|---|---|
| Python | 3.12.x | 3.12.3 (host), `python:3.12-slim-bookworm` (containers) |
| uv | latest at S0 | 0.12.19 (also pinned in `docker/python.Dockerfile` and CI) |
| Kafka image | `apache/kafka`, KRaft mode, 3.8+ or 4.x | `apache/kafka:4.1.2`, single-node combined broker+controller |
| confluent-kafka (py) | version supporting transactions + `send_offsets_to_transaction` | 2.15.1 (librdkafka 2.15.1) |
| Vector | version whose VRL has `sha2`, `encode_base64`, `get_enrichment_table_record`, and `uuid_v7` if available | `timberio/vector:0.58.0-debian` — A1 confirms the VRL function set and records any gap |
| ClickHouse | `clickhouse/clickhouse-server` LTS | `clickhouse/clickhouse-server:25.8` (LTS line) |
| immudb | ≥ 1.11 (PostgreSQL wire protocol) | `codenotary/immudb:1.11.2-bullseye-slim` |
| Wazuh | current stable single-node docker | 4.14.8 (indexer, manager, dashboard); certs from `wazuh/wazuh-certs-generator:0.0.2` |
| Ollama | latest | 0.20.3 (native host install, not in compose — D15) |
| LLM model (laptop) | 3–4B instruct, Q4, fits in 4 GB VRAM (e.g. `qwen2.5:3b`, or better per C4 bench) | `qwen2.5:3b` (Q4_K_M, ~1.9 GB). The host also carries `qwen2.5:7b-instruct`, which does **not** fit 4 GB VRAM; C4 benchmarks before any change |
| OCSF schema | latest stable 1.x | 1.9.0 |
| Node | ~~20 or 22 LTS~~ 25.x, build only (decision D17) | 25.2.1 / npm 11.7.0 <!-- synced from S0 --> |
| React / Vite / Tailwind | current majors | pinned by C5 when `console/package.json` is created |
| Caddy | 2.x | `caddy:2.11.4-alpine` |
| Drain3 | latest | 0.9.11 (pulls jsonpickle 1.5.1, cachetools 4.2.1) <!-- synced from C3 --> |
| google-re2 | latest | 1.1.20251105 |
| fastjsonschema | latest | 2.21.2 <!-- synced from A3 --> compiles the vendored OCSF subset to Python for the hot path (~10 µs/event vs ~216 µs for `jsonschema`, measured in A3). `jsonschema` stays for full error detail when an event is actually invalid. |
| Control-plane Python libs | — | sqlmodel 0.0.47, argon2-cffi 25.1.0, dulwich 1.2.15 (pure-Python git: the service image has no `git` binary), httpx 0.28.1 <!-- synced from C1/C2 --> |
| Other pinned Python libs | — | pydantic 2.13.5, pydantic-settings 2.15.0, jsonschema 4.26.0, charset-normalizer 3.5.1, uuid-utils 1.0.0, prometheus-client 0.26.0, clickhouse-connect 1.9.0, cryptography 50.0.1, zstandard 0.25.0, fastapi 0.141.1, uvicorn 0.54.0, ruff 0.16.9, pytest 9.1.1. `uv.lock` is the authority |
| Optional profiles | — | `openbao/openbao:2.4.1` (secure), `prom/prometheus:v3.7.3` + `grafana/grafana:12.4.1` (obs) |

---

## IF-NAMING — identifiers

| Thing | Format | Example |
|---|---|---|
| tenant_id | `t_<slug>` | `t_maha_power` |
| source_id | `src_<slug>` | `src_authsrv_01` |
| unregistered source | literal | `unregistered` |
| vendor | lowercase slug | `fortinet`, `linux`, `acme_ngfw`, `custom`, `unregistered` |
| contract id | slug | `authsrv` |
| contract ref | `<id>@<version>` | `authsrv@3` |
| api key id | `k_<8 base32>` | `k_7QX2MPLA` |
| api key secret | `veyra_<32 base62>` (shown once; stored as sha256+pepper) | — |
| event_uid | UUIDv7 string | `0192a4f0-...` |
| template_sig | `t_<12 hex>` | `t_3c85a1bfbf81` |
| segment_id | `seg_<topic>_<partition>_<first_offset>` | `seg_raw.linux_0_000000001200` |
| window_id | `w_<unix_start>` | `w_1790000000` |
| route_id | slug | `wazuh_main`, `partner_masked` |
| drift_id / draft_id | `d_<ulid>` / `dr_<ulid>` | — |
| zone | `dmz` \| `core` \| `ot` \| `external` | — |

---

## IF-PORTS — host ports

| Port | Service |
|---|---|
| 8080 | caddy (console + `/api/*`) |
| 8000 | control-api |
| 8100 | evidence-api (lineage + evidence) |
| 8300 | demo-engine |
| 8088 | ingest-gateway (HTTP push) |
| 5514/udp, 5515/tcp | edge-dmz syslog |
| 5524/udp, 5525/tcp | edge-core syslog |
| 9092 | kafka |
| 8123 | clickhouse HTTP |
| 3322, 5433 | immudb gRPC, pg-wire. <!-- synced from S0 --> Inside `veyra_net` the pg wire is 5432; the **host** mapping is `VEYRA_IMMUDB_PG_HOST_PORT` (default 5433), because a local PostgreSQL usually owns 5432 |
| 11434 | ollama |
| 8443 | wazuh dashboard |
| 8200 | openbao (optional) |
| 8201–8206 | metrics: normalizer, router, archiver, integrity, lineage-indexer, drift-worker |

Caddy routes:
- `/api/control/*` → control-api
- `/api/lineage/*` and `/api/evidence/*` → evidence-api
- `/api/demo/*` → demo-engine
- `/` → console static build

---

## IF-TOPICS — Kafka topics

Partitions and retention come from the profile (`03_INFRA_PROFILES.md`). Defaults shown as laptop / workstation.

| Topic | Key | Value | Partitions | Retention | Producer | Consumers |
|---|---|---|---|---|---|---|
| `raw.<vendor>` | source_id (salted `source_id#n` for heavy hitters) | IF-ENVELOPE | 3 / 12 per vendor | 3 d | edge, gateway | normalizer, archiver, lineage-indexer; control-api reads single records by `raw_ref` (assign-only, no group) for backtests and replays <!-- synced from C2 --> |
| `replay.raw` | source_id | IF-ENVELOPE + `replay` block | 3 / 12 | 1 d | control-api | normalizer |
| `norm.<category>` | event_uid | IF-NORM-EVENT | 3 / 12 | 3 d | normalizer | router, lineage-indexer |
| `lineage` | event_uid | IF-LINEAGE | 3 / 12 | 3 d | normalizer | lineage-indexer; control-api counts a replay job's records by `replay_job_id` (assign-only) <!-- synced from C2 --> |
| `dlq` | source_id | IF-DLQ | 1 / 6 | 14 d | normalizer | drift-worker, lineage-indexer |
| `shadow` | event_uid | IF-SHADOW | 1 / 6 | 1 d | normalizer | lineage-indexer |
| `vault_index` | event_uid | IF-VAULT-INDEX | 3 / 12 | 3 d | archiver | integrity, lineage-indexer |
| `receipts` | event_uid | IF-RECEIPT | 1 / 6 | 3 d | router | lineage-indexer |
| `control` | IF-CONTROL key | IF-CONTROL value | 1 | compacted, forever | control-api | normalizer, gateway, router |
| `audit` | actor | IF-AUDIT | 1 / 3 | 30 d | control-api, evidence-api | lineage-indexer |

`<category>` values: `system`, `findings`, `iam`, `network`, `discovery`, `application`, `uncategorized`.

Producer settings, required everywhere:
- `acks=all`
- `enable.idempotence=true`
- `compression.type=zstd`

The normalizer and archiver use transactions (`transactional.id = <service>-<instance>`). All consumers use `isolation.level=read_committed`.

---

## IF-ENVELOPE — raw envelope (JSON, UTF-8)

```json
{
  "v": 1,
  "event_uid": "0192a4f0-0000-7000-8000-000000000001",
  "tenant_id": "t_maha_power",
  "source_id": "src_authsrv_01",
  "vendor": "custom",
  "zone": "dmz",
  "collector_id": "edge-dmz-01",
  "transport": "syslog_udp",
  "peer_ip": "172.20.0.15",
  "peer_port": 40112,
  "listener": "dmz-udp",
  "received_time": "2026-09-26T08:35:11.123456789Z",
  "seq_no": null,
  "raw_sha256": "42d88deeb474802a9dce2c9971f2613aa1a95cec8b0102011fc88a8c2894ab57",
  "raw_len": 177,
  "raw_b64": "PDEzND5TZXAg...",
  "framing": {"method": "datagram", "truncated": false, "parts": 1},
  "custody": "realtime",
  "auth": {"method": "ip_map", "key_id": null},
  "salt": null
}
```

Rules:
- `raw_sha256` = SHA-256 over the exact bytes in `raw_b64` after base64-decoding. Computed at the edge or gateway, never recomputed downstream except for verification.
- `transport` is one of: `syslog_udp`, `syslog_tcp`, `http_hec_raw`, `http_hec_event`, `http_batch`, `kafka`.
- `framing.method` is one of: `datagram`, `newline`, `octet_counting`, `multiline_join`, `http_body`, `batch_line`.
- `framing.truncated` is true if the size cap (`VEYRA_MAX_EVENT_BYTES`) cut the event. The archived copy is still the full bytes when available.
- `custody` is `realtime` or `post_hoc` (batch upload).
- `auth.method` is one of: `ip_map`, `api_key`, `mtls`, `none`.
- Unregistered sources: `tenant_id="unassigned"`, `source_id="unregistered"`, `vendor="unregistered"`. Topic `raw.unregistered`.
- The Kafka key is `source_id`. When a source is flagged as a heavy hitter (`IF-CONTROL` source flag `salt_buckets>1`), the key is `source_id#<n>` and `salt=n`.
- Production target: Protobuf (`packages/veyra_common/proto/envelope.proto`, written in S0 for the slide). The demo wire format is JSON.

---

## IF-INVENTORY — edge source inventory (Vector enrichment table)

File `edge/vector/inventory/sources.csv`, written by control-api (C1) and read by Vector (A1).

```
listener,match_kind,match_value,source_id,tenant_id,vendor,zone
dmz-tcp,peer_ip,172.20.0.21,src_fw_dmz_01,t_ntro_core,acme_ngfw,dmz
core-udp,syslog_host,core-lnx-07,src_lnx_core_07,t_ntro_core,linux,core
dmz-tcp,syslog_host,fw01,src_authsrv_01,t_maha_power,custom,dmz
```

Resolution (v1 §6 step 2: network + message fingerprint) tries two lookups in order:
1. `(listener, peer_ip)`
2. `(listener, syslog_host)`, where the host is extracted cheaply from the syslog header with `^<\d+>(?:\d\s)?\S+\s+\S+\s+\S+\s+(\S+)` for 3164, with a 5424 variant.

If neither matches, the event is unregistered. The `syslog_host` fingerprint lets one demo sender container simulate many devices. HTTP sources are resolved by API key in the gateway, not through this table.

**Reload mechanism (verified in A1, Vector 0.58.0):** control-api writes `sources.csv.tmp` and renames it atomically — that part matters, it is what stops Vector reading a half-written file. <!-- synced from A1 --> Vector **watches the enrichment-table CSV itself** when started with `--watch-config`, so a new row is live within 5 s with **no** `reload.stamp` touch and **no** container restart; the stamp file is kept as an inert hook in case a future version stops watching enrichment tables. The A1 fallback (control-api restarting the edge over the Docker API) is therefore not needed on this version. Measurements and the caveat that a reload drops in-flight events are in `edge/RELOAD.md`; batch inventory writes rather than rewriting per source.

---

## IF-CONTROL — control topic messages (compacted)

Key → value (JSON). A `null` value is a tombstone.

| Key | Value |
|---|---|
| `contract:<id>` | `{"id","version","state","tenant_id","sources":[...],"compiled":{...IF-CONTRACT-COMPILED},"candidate":{version, compiled} or null,"published_at"}` |
| `apikey:<key_id>` | `{"key_id","secret_sha256","pepper_id","source_id","tenant_id","status":"active\|revoked","quota_eps":int,"created_at"}` |
| `source:<source_id>` | `{"source_id","tenant_id","vendor","zone","transport","contract_id","expected_eps":float,"salt_buckets":int,"status":"active\|paused"}` |
| `vocab:<name>` | `{"name","version","entries":{"FAILED":{"status_id":2},...}}` |
| `enrich:<table>` | `{"name","version","rows":[...]}` (small tables only: asset inventory, zone map) |
| `routes` | full IF-ROUTES document |

Consumers rebuild their state by reading the compacted topic from the beginning at startup, then keep following it.

<!-- synced from C1/C2 --> Details fixed by control-api:
- `apikey:*`: `secret_sha256 = sha256(pepper_bytes + secret_utf8)`, where the pepper is the 64 hex characters in `<data_dir>/keys/api_pepper` and `pepper_id = "p_" + sha256(pepper)[:8]`. The gateway (A2) must hash identically.
- `source:*`: `transport` is IF-ENVELOPE's vocabulary; control-api's `http_push` is published as `http_hec_event`.
- `contract:*`: published only once a contract has an active version. A brand-new contract's first version is not on `control` while it is a canary; it appears when promoted.
- `/internal/reset` tombstones every key the pre-reset state had published that the seed does not have.

---

## IF-CONTRACT-YAML — Log Contract (v1 "Source Pack")

Stored in the contract registry, a separate git repository checked out next to the code at `../contracts-repo` (path: `VEYRA_CONTRACTS_REPO`), as `<tenant_id>/<contract_id>.yaml`. Versions are git commits plus the `version:` field. <!-- synced: registry moved out of the code repo (05_CHANGELOG, 2026-09-27) -->

```yaml
contract: authsrv
version: 3
tenant: t_maha_power
sources: [src_authsrv_01]
description: Maha Power auth server (custom app, pipe-delimited syslog with JSON body)
state: active            # draft | testing | canary | active | retired

envelope:                # peel order; each layer exposes fields to the next
  - syslog: {variant: auto}          # auto | rfc3164 | rfc5424 | none
  - json: {text_field: msg}          # parse body as JSON; the template text is .msg
  # other layers: kv {pair_sep: " ", kv_sep: "="}, cef {}, leef {}, csv {delimiter: ",", header: [...]},
  #               regex {pattern: "..."}, base64 {field: ...}

time:
  field: syslog.timestamp            # or json.ts, or a template capture
  formats: ["%b %d %H:%M:%S"]        # tried in order; empty = auto-detect
  timezone: Asia/Kolkata
  year: infer_from_received          # or: present

templates:                           # matched against the text_field (or the whole body if none)
  - id: auth_failed
    pattern: 'user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>'
    class: authentication            # OCSF class name from IF-OCSF-SUBSET
    activity: logon
    map:
      user.name: $user
      src_endpoint.ip: $src_ip
      dst_endpoint.ip: $dst_ip
      status_id: {const: 2}          # Failure
      severity_id: {const: 3}
      message: $__text               # special: the whole template text
    unmapped: [attempts]
  - id: auth_ok
    pattern: 'user=<user> OK login from <src_ip:ip> via <dst_ip:ip>'
    class: authentication
    activity: logon
    map: {user.name: $user, src_endpoint.ip: $src_ip, dst_endpoint.ip: $dst_ip, status_id: {const: 1}}

required: [time, user.name, src_endpoint.ip]
enrich: [asset_inventory, zone_map]
pii: [user.name, src_endpoint.ip]
vocab: [status_words]
tests:
  - sample: samples/authsrv/failed_1.log
    expect: expected/authsrv/failed_1.json
provenance:
  drafted_by: llm:qwen2.5:3b        # or heuristic | human | library
  draft_id: dr_01J...
  approved_by: [author@maha, approver@veyra]
```

**Pattern syntax** (compiled to an RE2 regex with named groups, anchored at both ends):

| Token | Meaning | Regex |
|---|---|---|
| `<name>` | one non-space token | `(?P<name>\S+)` |
| `<name:ip>` | IPv4/IPv6 | IPv4 `\d{1,3}(?:\.\d{1,3}){3}` or IPv6 pattern |
| `<name:int>` | integer | `-?\d+` |
| `<name:word>` | letters, digits, `._-` | `[\w.\-]+` |
| `<name:rest>` | rest of line | `.*` |
| `<name:quoted>` | double-quoted string (quotes excluded from capture) | `"(?P<name>[^"]*)"` |
| `<*>` | anonymous token | `\S+` |

Literal text is regex-escaped. Runs of whitespace match `\s+`.

**Map value forms:**
- `$capture`: a template capture.
- `$json.path` or `$syslog.host`: a field exposed by an envelope layer.
- `{const: X}`: a constant, validated against OCSF enums.
- `{vocab: status_words, from: $status}`: a vocabulary lookup.
- `{ts: $capture, formats: [...]}`: a timestamp parse.

**Lifecycle:** `draft → testing (golden tests run) → canary (backtest + live shadow) → active → retired`.
- Promotion to `active` requires four-eyes: approver ≠ author (enforced by control-api).
- Only one `active` and at most one `canary` version per contract.

---

## IF-CONTRACT-COMPILED — what the engine consumes

Produced by `veyra_contracts.compile(yaml_text) -> CompiledContract` (C2), serialized as JSON into IF-CONTROL.

```
{contract, version, tenant, sources, envelope:[layer specs], time:{...},
 templates:[{id, regex (RE2 string), captures:[{name,type}], class_uid, activity_id, type_uid, category,
             map:[{ocsf_path, kind:"capture|field|const|vocab|ts|text", ref, value, vocab}], unmapped:[...]}],
 required:[...], enrich:[...], pii:[...], vocab:[...], compiled_at, compiler_version}
```

---

## IF-ENGINE-LIB — `veyra_engine` Python API (owner A; stub in S0)

```python
from veyra_engine import Engine, EngineContext, NormResult, extract_tokens, template_sig

engine = Engine(ctx: EngineContext)          # ctx: vocab tables, enrich tables, settings (limits, budgets)
engine.load(compiled: list[CompiledContract]) # replaces the active set atomically
engine.set_candidate(compiled: CompiledContract | None, contract_id: str)
res: NormResult = engine.normalize(envelope: Envelope, *, use_candidate: bool = False)
# NormResult: ocsf: dict, ulpf: dict, tier: int, conformance: str, category: str,
#             dlq: DlqRecord | None, timings_us: dict, parse_path: list[str]

toks = extract_tokens(text: str) -> list[Token]   # Token(id:"k3", value, start, end, kind, key: str|None)
sig  = template_sig(scope: str, text: str) -> str # IF-TEMPLATE-SIG
peel = engine.peel(envelope) -> PeelResult        # layers, fields with byte spans, text_field value + span
```

`normalize` is pure: no I/O, no clock reads except those passed in `envelope.received_time`. The control plane uses the same library for golden tests, backtests and onboarding previews, so results are identical to runtime.

---

## IF-OCSF-SUBSET — classes and fields the demo supports

Pin the OCSF version in IF-VERSIONS and vendor the JSON schema for these classes into `packages/veyra_engine/ocsf/`.

<!-- synced from A3 --> **Verified against OCSF 1.9.0** (`https://schema.ocsf.io/api/1.9.0/classes/<name>`, 2026-09-27): every `class_uid`, `category_uid`, activity id and the `severity_id` / `status_id` / `disposition_id` / `action_id` enums below match, and `type_uid = class_uid * 100 + activity_id` holds. Two deliberate differences, both in `packages/veyra_engine/ocsf/README.md`: OCSF marks **`cloud` and `osint` required** on several classes and VEYRA neither emits nor validates them (a log pre-processor does not invent cloud metadata, and nothing downstream needs them), and the vendored schema constrains only the mapped catalogue while leaving `additionalProperties` open, so its job is to catch a *wrong* value rather than to enumerate OCSF.

| Class | class_uid | category (topic) | Activities used |
|---|---|---|---|
| Base Event | 0 | uncategorized | 0 Unknown, 99 Other |
| Process Activity | 1007 | system | 1 Launch, 2 Terminate |
| Authentication | 3002 | iam | 1 Logon, 2 Logoff |
| Network Activity | 4001 | network | 1 Open, 2 Close, 5 Refuse, 6 Traffic |
| HTTP Activity | 4002 | network | 1 Connect … 99 Other (verify ids against pinned schema) |

`type_uid = class_uid * 100 + activity_id`.

**Mappable field catalogue.** The LLM and UI may only target these fields; extend additively.
- **Common:** `time`, `message`, `severity_id`, `status_id`, `status_detail`, `disposition_id`, `action_id`, `metadata.product.name`, `metadata.product.vendor_name`.
- **Endpoints:**
  - `src_endpoint.ip`, `src_endpoint.port`, `src_endpoint.hostname`
  - `dst_endpoint.ip`, `dst_endpoint.port`, `dst_endpoint.hostname`
  - `device.hostname`, `device.ip`
- **Identity:** `user.name`, `user.uid`, `user.domain`, `actor.user.name`.
- **Network:** `connection_info.protocol_name`, `traffic.bytes_in`, `traffic.bytes_out`.
- **HTTP:** `http_request.url.path`, `http_request.http_method`, `http_response.code`.
- **Process:** `process.name`, `process.pid`, `process.cmd_line`.
- **Observables** are generated automatically from typed fields: `observables[]{name,type_id,value}`.
- **Always present:** `raw_data` (the decoded raw text) and `unmapped` (object).

Enums used (verify against the pinned schema; fix here if they differ):
- `severity_id`: 0 Unknown, 1 Informational, 2 Low, 3 Medium, 4 High, 5 Critical, 6 Fatal.
- `status_id`: 0 Unknown, 1 Success, 2 Failure, 99 Other.
- `disposition_id`: 1 Allowed, 2 Blocked (plus others per schema).

---

## IF-ULPF — lineage extension on every normalized event

```json
"ulpf": {
  "v": 1,
  "event_uid": "…", "tenant_id": "…", "source_id": "…", "vendor": "…", "zone": "dmz",
  "raw_ref": {"topic": "raw.custom", "partition": 1, "offset": 4412},
  "raw_sha256": "…", "received_time": "…", "custody": "realtime",
  "contract": {"id": "authsrv", "version": 3} ,
  "template": {"sig": "t_3c85a1bfbf81", "id": "auth_failed"},
  "tier": 1,
  "conformance": "match",
  "parse_path": ["syslog:rfc3164", "json", "template:auth_failed"],
  "field_offsets": {"user.name": [58, 64], "src_endpoint.ip": [82, 91]},
  "derived_fields": {"status_id": "const", "time": "ts:inferred_year"},
  "class_hint": {"class_uid": 3002, "confidence": "low"},
  "time": {"source": "event", "tz_assumed": "Asia/Kolkata", "year_inferred": true, "clock_skew_ms": -812},
  "encoding": {"detected": "utf-8", "confidence": 0.99, "invalid_bytes": 0},
  "pii_fields": ["user.name", "src_endpoint.ip"],
  "revision": 1, "supersedes": null, "replay": false, "shadow": false,
  "engine_version": "0.3.0"
}
```

Conformance values by tier:

| tier | conformance | Meaning |
|---|---|---|
| 1 | `match` | Template matched, required fields present, schema valid |
| 2 | `partial` | Envelope parsed and/or template matched, but required fields are missing or validation partly failed |
| 3 | `unknown_template` | No template matched; generic extraction applied (observables, unmapped, class_hint) |
| 4 | `unparseable` | Nothing extractable, or a budget, size or crash guard tripped |

- `contract` is null when no contract applies (unregistered, or no contract for the source).
- `class_hint` is present only at tier 3.
- `field_offsets` are byte offsets into the decoded raw bytes. `[start, end)` satisfies `raw[start:end] == value as it appears in the raw` (verified by `provenance_check`). Fields absent from `field_offsets` must appear in `derived_fields`.
- `revision` starts at 1. A replay emits `revision = previous + 1` and `supersedes = "<event_uid>@<prev_revision>"`.

---

## IF-NORM-EVENT — value on `norm.<category>`

The OCSF event object, with `ulpf` as above, `raw_data`, and `unmapped`. Tier 3/4 events use class 0 (Base Event) unless a validated class applies. Example (tier 3):

```json
{"class_uid":0,"category_uid":0,"type_uid":99,"activity_id":99,"severity_id":0,"time":1790400311000,
 "message":"user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3",
 "raw_data":"<134>Sep 26 14:05:11 fw01 app[233]: {…} | trace=\n  at com.x.Auth.login(Auth.java:88)",
 "observables":[{"name":"ip_1","type_id":2,"value":"45.12.3.9"},{"name":"ip_2","type_id":2,"value":"10.2.3.4"},{"name":"user","type_id":4,"value":"neel.k"}],
 "unmapped":{"user":"neel.k","attempts":"3","syslog.host":"fw01","syslog.app":"app","trace":"at com.x.Auth.login(Auth.java:88)"},
 "metadata":{"version":"<ocsf>","product":{"name":"VEYRA"}},
 "ulpf":{ "...": "tier 3, conformance unknown_template, class_hint 3002 low" }}
```

---

## IF-LINEAGE / IF-DLQ / IF-SHADOW / IF-VAULT-INDEX / IF-RECEIPT / IF-AUDIT

```
IF-LINEAGE   {event_uid, revision, tenant_id, source_id, raw_ref{topic,partition,offset}, raw_sha256,
              contract_ref|null, template_sig, template_id|null, tier, conformance, class_uid, category,
              norm_topic, produced_at, replay:bool, replay_job_id|null,
              search_terms:[str] (≤16: IPs, users, hostnames extracted, for lineage search)}
IF-DLQ       {event_uid, tenant_id, source_id, tier, reason_code, reason_detail, contract_ref|null,
              template_sig, text_masked (PII-masked template text, ≤ 2 KB), parse_path, produced_at}
              reason_code ∈ {no_contract, no_template_match, required_missing, schema_invalid,
                             decode_error, budget_exceeded, size_exceeded, engine_crash}
IF-SHADOW    {event_uid, contract_id, active_ref, candidate_ref, active_tier, candidate_tier,
              changed_fields:[ocsf_path], regressions:[ocsf_path], produced_at}
IF-VAULT-INDEX emitted only at seal, in the same Kafka transaction that commits the archiver's offsets:
              per event:   {kind:"event", event_uid, raw_topic, partition, offset, segment_id, record_idx, chain_hash_hex, sealed_at}
              per segment: {kind:"segment", segment_id, raw_topic, partition, first_offset, last_offset, record_count,
                            prev_chain_hash_hex, last_chain_hash_hex, segment_digest_hex, sealed_at}
IF-RECEIPT   {event_uid, revision, route_id, status: delivered|filtered|failed, detail, at}
IF-AUDIT     {actor, role, action, target, detail, at}
```

---

## IF-TEMPLATE-SIG — deterministic template signature (frozen)

Input: `scope` (contract id, else vendor, else `unregistered`) and `text` (the template text: the `text_field` value after peeling, else the body).

1. Split `text` on whitespace.
2. Mask each token using the first rule that matches:
   1. UUID → `<UUID>`
   2. IPv4 (optional `:port`) → `<IP>`
   3. timestamp-like `^\d{1,4}[-/:T]\d{1,2}([-/:T.]\d{1,4})*Z?$` → `<TS>`
   4. IPv6 (contains `::` or ≥ 3 colons, hex/colon chars only) → `<IP>`
   5. email → `<EMAIL>`
   6. `key=value` → `key=<V>`
   7. `key:value` → `key:<V>`
   8. hex of length ≥ 8 → `<HEX>`
   9. contains a digit → `<NUM>`
   10. otherwise, the token unchanged.
3. `masked = " ".join(tokens)`.
4. `sig = "t_" + sha256((scope + "\x1f" + masked).encode("utf-8")).hexdigest()[:12]`.

Test vectors (from `reference/spec_vectors.py`):

| scope | text | masked | sig |
|---|---|---|---|
| authsrv | `user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3` | `user=<V> FAILED login from <IP> via <IP> attempts:<V>` | `t_3c85a1bfbf81` |
| authsrv | `user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1` | same | `t_3c85a1bfbf81` |
| unregistered | `Sep 26 14:05:11 conn 88213 closed by 10.0.0.5` | `Sep <NUM> <TS> conn <NUM> closed by <IP>` | `t_940f551bf008` |

---

## IF-CHAIN — per-partition hash chain (frozen; v1 §8.2)

```
h0 = SHA256("VEYRA-GENESIS" || 0x1f || topic_utf8 || 0x1f || str(partition)_utf8)
hn = SHA256(h(n-1) || raw_sha256_bytes(32) || event_uid_bytes(16, UUID big-endian) || offset_uint64_be(8))
```

The chain runs over `raw.*` records in offset order per `(topic, partition)`. The archiver persists the chain head per partition. Test vectors for topic `raw.acme`, partition 0:

| step | event_uid | offset | raw_sha256 | h |
|---|---|---|---|---|
| h0 | — | — | — | `a93423deac995ba0e328e4dadbe29d43c77d61f273f1f0d0410b65ad6cf41f53` |
| 1 | …0001 | 100 | `42d88dee…94ab57` | `aa0fe44eda2a9379e4e60997c65579b0a747adfd0f835199190df147cb7d2c2d` |
| 2 | …0002 | 101 | `d0d2cdd3…dba4dc` | `d361f24129e5f0063c8420b399f7295b0bc7e9e61e2f8a9e4c9310d9c33b8949` |
| 3 | …0003 | 102 | `9703bf6c…35cea4` | `dea0f94c4673dca513810832005ceeb966e26e26a30a638d61e2bbcfa0da6b97` |

Full raw inputs are in `reference/spec_vectors.py`.

---

## IF-SEGMENT — vault segment format

Plaintext record blob, then zstd, then AES-256-GCM.

- **Record:** `u32 len_be || envelope_json_bytes`. The full envelope is stored, including `raw_b64`.
- **Segment file** `data/vault/<topic>/<partition>/<segment_id>.seg`:
  - `magic "VEYRASEG1"`
  - `u32 header_len`
  - `header_json {segment_id, topic, partition, chain_epoch, first_offset, last_offset, record_count, prev_chain_hash_hex, last_chain_hash_hex, blob_sha256_hex, key_id, wrapped_dek_b64, nonce_b64, zstd_level, created_at, sealed_at}`
  - `ciphertext`
- **Segment digest (Merkle leaf data):**
  `SHA256("VEYRA-SEG" || 0x1f || segment_id || 0x1f || prev_chain_hash(32) || last_chain_hash(32) || record_count_u64_be || blob_sha256(32))`
- `chain_epoch` starts at 0. It is incremented only if the archiver has to restart a chain after an unrecoverable anomaly (see B2). An epoch change starts from a new genesis `h0` that includes the epoch: `"VEYRA-GENESIS" || 0x1f || topic || 0x1f || partition || 0x1f || epoch`, where epoch 0 omits the suffix, so the frozen vectors hold.
- After sealing: file mode 0444. If `VEYRA_VAULT_CHATTR=1` and the process has `CAP_LINUX_IMMUTABLE`, also run `chattr +i`.

## IF-MERKLE — window tree (frozen; RFC 6962 hashing)

- `leaf = SHA256(0x00 || data)`
- `node = SHA256(0x01 || left || right)`
- Split point: `k` = largest power of 2 less than `n`.
- Leaves are the segment digests of all segments **sealed** within `[window_start, window_end)`, ordered by `(sealed_at, segment_id)`.

Test vector: leaf data `sha256("segment-0")`, `sha256("segment-1")`, `sha256("segment-2")` gives root `49a27ff0ee8487600104ea234555fdaa1b3f8ac98d67d12fa3c3620a0e7000f4`.

## IF-SIGNED-ROOT — chained, signed window root

Canonical JSON: sorted keys, no whitespace, UTF-8.

```json
{"v":1,"window_id":"w_1790000000","window_start":1790000000,"window_end":1790000060,
 "leaf_count":7,"root":"<hex>","segments":["seg_…", "…"],"prev_signed_sha256":"<hex of sha256(prev canonical payload)>",
 "key_id":"veyra-root-ed25519-1","alg":"Ed25519"}
```

- Signature: Ed25519 over the canonical payload bytes, via KeyProvider.
- Stored in immudb (key `root:<window_id>`, value = `{payload, sig_b64}`) using a **verified set**. Also appended to `data/vault/roots/ledger.ndjson`.
- B3 decides the client path after a spike: the immudb Python SDK (verifiedSet/verifiedGet), or pg-wire SQL into a `roots` table plus an SDK or `immuclient` verification. The chosen path is recorded here.
- The immudb database name comes from `data/state/immudb_db` (default `veyra`). The demo reset creates a fresh database per run, because immudb cannot delete data.
- Empty windows still produce a root (`leaf_count` 0, root = SHA256 of the empty string), so the chain has no gaps.

## IF-KEYPROVIDER — key interface (`veyra_evidence.keys`)

```python
class KeyProvider(Protocol):
    def new_data_key(self) -> tuple[bytes, str, bytes]    # (plaintext_dek, key_id, wrapped_dek)
    def unwrap(self, key_id: str, wrapped: bytes) -> bytes
    def sign(self, key_id: str, payload: bytes) -> bytes   # Ed25519
    def public_key_pem(self, key_id: str) -> str
```

Implementations:
- `local`: master KEK and Ed25519 key in `data/keys/`, mode 0400, generated on first boot.
- `openbao`: transit engine; wrap/unwrap and sign with an ed25519 transit key.

Selected by `VEYRA_KEY_PROVIDER`.

---

## IF-CH-SCHEMA — ClickHouse (owner B)

Database `veyra`. Default TTL: `VEYRA_LINEAGE_TTL_DAYS` (90).

| Table | Engine | Key columns | Filled from |
|---|---|---|---|
| `raw_events` | MergeTree ORDER BY (tenant_id, source_id, received_time) | event_uid, raw_ref, raw_sha256, transport, zone, custody, raw_len, framing, received_time | `raw.*` |
| `norm_lineage` | ReplacingMergeTree(produced_at) ORDER BY (event_uid, revision) | event_uid, revision, tenant_id, source_id, tier, conformance, contract_ref, template_sig, template_id, class_uid, category, produced_at, replay, replay_job_id, search_terms Array(String) (with a bloom_filter skip index) | `lineage` |
| `norm_events` | ReplacingMergeTree ORDER BY (event_uid, revision) | event_uid, revision, ocsf_json String CODEC(ZSTD) | `norm.*` |
| `window_roots` | ReplacingMergeTree ORDER BY window_id | window_id, window_start, window_end, leaf_count, root, prev_signed_sha256, sig_b64, immudb_tx, immudb_verified | integrity (direct insert) |
| `vault_locations` | ReplacingMergeTree ORDER BY event_uid | event_uid, segment_id, record_idx, chain_hash, sealed, sealed_at | `vault_index` |
| `segments` | ReplacingMergeTree ORDER BY segment_id | segment_id, topic, partition, first/last offset, record_count, digest, sealed_at, window_id | `vault_index` (sealed) + integrity |
| `receipts` | MergeTree ORDER BY (event_uid, route_id) | event_uid, revision, route_id, status, at | `receipts` |
| `dlq_events` | MergeTree ORDER BY (source_id, template_sig, produced_at) | IF-DLQ fields | `dlq` |
| `shadow_diffs` | MergeTree ORDER BY (contract_id, produced_at) | IF-SHADOW fields | `shadow` |
| `audit_log` | MergeTree ORDER BY at | IF-AUDIT | `audit` |
| `mv_source_minute` | AggregatingMergeTree (materialized view) | per (tenant, source, minute): raw count, tier1..4 counts, bytes, max received_time | raw_events + norm_lineage |
| `mv_route_minute` | AggregatingMergeTree (MV) | per (route, minute): delivered, failed, filtered | receipts |

Queries used by the APIs live in `packages/veyra_lineage/queries.py` as named functions (one per endpoint). No SQL is written inside API handlers.

---

## IF-API-CONTROL — control-api (`/api/control`, owner C)

Auth: session cookie. Roles: `admin`, `pack_author`, `pack_approver`, `org_viewer`, `auditor`. Each user is bound to a tenant or to `*` (platform).

```
POST /auth/login {email,password} → {user, role, tenant}      POST /auth/logout      GET /auth/me
POST /auth/demo-switch {email} → {user, role, tenant}   (demo mode only; else 404)
GET/POST /tenants                     GET/PATCH /tenants/{id}
GET/POST /sources                     GET/PATCH /sources/{id}      (POST triggers inventory + control publish)
POST /sources/{id}/keys → {key_id, secret (once), endpoints{hec_url, batch_url, syslog{host,port,listener}}, curl_example}
GET  /sources/{id}/keys → [{key_id, source_id, status, quota_eps, created_by, created_at, revoked_at}]
POST /keys/{key_id}/revoke
POST /onboarding/analyze {tenant_id, source_name, transport, samples:[str]} → {classification, peel preview,
      templates:[{sig, drain_template, count, tokens:[Token]}], library_match|null, draft_id}
GET  /drafts/{id} → {draft (IF-LLM-DRAFT output), provenance:[{ocsf_path, ok, reason}], yaml_preview, backtest}
PATCH /drafts/{id} {mappings edits} → re-run provenance + backtest
POST /drafts/{id}/submit → contract version in state testing → golden tests → canary
GET/POST /contracts, GET /contracts/{id}, GET /contracts/{id}/versions/{v}, GET /contracts/{id}/diff?from=&to=
POST /contracts/{id}/versions/{v}/approve   (403 if approver == author)
POST /contracts/{id}/versions/{v}/promote   (canary → active; publishes control)
POST /contracts/{id}/rollback {to_version}
POST /contracts/{id}/versions/{v}/backtest {template_sigs?, samples?} → backtest result (also run automatically at canary)
GET  /drift?state=open → [{drift_id, source_id, template_sig, drain_template, count, first_seen, last_seen, samples_masked, draft_id|null}]
GET  /drift/{id} → one item (+ related_sigs, sample_event_uids, state, resolved_by)      POST /drift/{id}/dismiss
POST /drift/{id}/draft → starts a draft (LLM or heuristic per settings)
POST /replay {contract_id, template_sigs?, source_id?, from?, to?} → {job_id}      GET /replay/{job_id} → progress
GET  /replay?contract_id= → jobs, newest first
GET  /routes        GET /audit
GET  /stream (SSE): events {type: overview|drift|draft|contract|replay|source, data}
     <!-- synced from C2/C3 --> contract: {id, version, state, action}; replay: {job_id, contract_id, status, total, published, normalized, detail}; drift: {drift_id, source_id, template_sig, count, state, created}

Internal (docker network only, not routed by caddy):
POST /internal/drift {source_id, template_sig, drain_template, count, samples_masked, first_seen, last_seen,
      related_sigs, sample_event_uids} (drift-worker upsert; 404 for an unknown source) <!-- synced from C3 -->
POST /internal/reset {scenario} → wipe SQLite + reset the contract registry (../contracts-repo) to seed tag + reseed + republish control   (demo-engine)
GET  /internal/demo/last-key → {key_id, secret, source_id}   (only when VEYRA_DEMO_MODE=1; lets demo-engine use the key issued live)
```

## IF-API-EVIDENCE — evidence-api (`/api/lineage`, `/api/evidence`, owner B)

```
GET /lineage/overview?tenant= → {eps_1m, totals_by_tier, sources:[…], routes:[…], vault:{segments, last_root}}
GET /lineage/sources?tenant= → Source Health rows (expected vs actual EPS, last_seen, tier mix, contract ref, clock skew p50)
GET /lineage/search?q=&tenant=&limit= → matches on event_uid, sha, ip, user, template_sig
GET /lineage/events/{event_uid} → {raw (decoded text + b64), envelope, revisions:[{revision, ocsf, ulpf}], vault location, receipts}
GET /lineage/templates/{sig}/events?limit= → event_uids (used by replay)
GET /evidence/verify/{event_uid} → {ok, steps:[{id, label, ok, detail, ms}]}
       step ids: fetch_raw, hash_raw, decrypt_segment, chain_walk, segment_digest, merkle_inclusion, root_signature, immudb_verified
POST /evidence/export/{event_uid} → zip (raw.bin, envelope.json, proof.json, signed_root.json, pubkey.pem, verify.py, SECTION63_TECHNICAL.md)
GET /evidence/roots?limit= → signed roots with immudb verification status
GET /evidence/pubkey
GET /lineage/stream (SSE): overview ticks
```

## IF-API-DEMO — demo-engine (`/api/demo`, owner B)

```
POST /reset → wipe + reseed to pre-demo state (async; GET /reset/status)
POST /stage/{n} → runs scenario stage n (1..6), returns immediately; GET /stage/status
POST /tamper {mode: naive_flip | insider_rewrite | segment_delete | root_rewrite, event_uid?} → {target, detail}
POST /untamper → restores from the pristine copy (demo only)
GET  /scenario → the loaded scenario with stage descriptions
GET  /preflight → [{check, status: PASS|WARN|FAIL, detail}]
(drift-worker, internal, :8206) POST /flush → forces drift emission for all open groups (stage 4 fallback)
(drift-worker, internal, :8206) POST /reset → forgets every group and cluster; control-api's /internal/reset calls it <!-- synced from C3 -->
```

## IF-LLM-DRAFT — drafter I/O (owner C)

**Request** (built by control-api):
```json
{"template_sig":"t_…","drain_template":"user=<*> FAILED login from <*> via <*> attempts:<*>",
 "samples_masked":["user=<USER_1> FAILED login from 45.12.3.9 …", "…"],
 "tokens":[{"id":"k1","value":"neel.k","kind":"kv_value","key":"user"},{"id":"k4","value":"45.12.3.9","kind":"ip"},"…"],
 "allowed_classes":[…IF-OCSF-SUBSET names…], "allowed_fields":[…catalogue…], "enums":{…}}
```

**Response** (Ollama JSON-schema constrained):
```json
{"class":"authentication","activity":"logon","confidence":"high",
 "mappings":[{"ocsf_path":"user.name","token":"k1"},{"ocsf_path":"src_endpoint.ip","token":"k4"},
             {"ocsf_path":"status_id","const":2}],
 "rationale":"short text"}
```

- Mappings may reference only `token` ids or `const` values from `enums`. Never free text.
- The drafter then generalizes the tokens into a template `pattern` (captures named from `ocsf_path`) and runs `provenance_check` and a backtest.
- Modes (`VEYRA_LLM_MODE`): `live` | `cache` | `live_then_cache` | `heuristic`. The cache is keyed by `template_sig`, in `data/llm_cache/`.

## IF-ROUTES — routes.yaml (owner A)

```yaml
routes:
  - id: wazuh_main
    filter: {tenants: ["*"], tiers: [1,2,3,4]}
    format: ocsf_json
    masking: none
    sink: {type: ndjson_file, path: /sinks/wazuh/veyra.ndjson, fsync_ms: 200}
  - id: partner_masked
    filter: {tenants: [t_maha_power], classes: [3002, 4001]}
    format: ocsf_json
    masking: {user.name: hmac, src_endpoint.ip: hmac, raw_data: redact}
    sink: {type: ndjson_file, path: /sinks/partner/partner.ndjson}
```

Sink types: `ndjson_file` | `syslog_tcp` (host, port; used for remote Wazuh) | `http_json` (url).

## IF-WAZUH — Wazuh integration (owner A)

- The manager reads `/sinks/wazuh/veyra.ndjson` via `<localfile><log_format>json</log_format>`.
- Each line is one IF-NORM-EVENT plus convenience fields `veyra.tier`, `veyra.class`, `veyra.tenant`, `veyra.source`.
- The custom rules file `wazuh/rules/veyra_rules.xml` uses rule id range **100100–100199**.
  <!-- synced from S0 --> **100100 must be a child of Wazuh's built-in rule 99000**
  (`<if_sid>99000</if_sid>`), and 100110–100130 children of 100100. Wazuh ships 99000
  ("Amazon Security Lake rules grouped", level 0) matching any json-decoded event that carries
  `activity_id` and `category_uid` — i.e. every OCSF event, so every VEYRA event. A sibling rule
  loses to it, and because 99000 is level 0 no alert is generated at all. The event payload is
  unaffected; only the rule tree changes. Verified on Wazuh 4.14.8 with `wazuh-logtest`.

| Rule id | Level | Matches |
|---|---|---|
| 100100 | 3 | Any VEYRA event (so every event is visible in alerts) |
| 100110 | 5 | Authentication failure (`class_uid` 3002 and `status_id` 2) |
| 100111 | 10 | Brute force: frequency 5 within 60 s, same `src_endpoint.ip`, on 100110 |
| 100120 | 3 | tier 3 `unknown_template` |
| 100121 | 4 | tier 4 `unparseable` |
| 100130 | 3 | Corrected event (`ulpf.revision` > 1) |

---

## IF-ENV — environment conventions

- All settings are prefixed `VEYRA_` and loaded by `veyra_common.settings.Settings` (pydantic-settings).
- Profiles set defaults (see `03_INFRA_PROFILES.md` for the full knob table).
- Each service reads only the keys it declares.
