# C1 — Control API foundation

```
track: C   owner: C   status: todo
contracts: v1.4
depends_on: [S0]     unblocks: [CP1, A2, C2, B7]
consumes: [IF-NAMING, IF-TOPICS, IF-ENV]
provides: [IF-API-CONTROL (auth, tenants, sources, keys, audit, stream, internal), IF-CONTROL (publisher), IF-INVENTORY (writer), IF-AUDIT]
directories: [services/control_api/]
```

## Goal

The governed core of the control plane:
- users and roles, tenants, sources, API keys;
- the single publisher of the compacted `control` topic;
- the writer of the edge inventory;
- the audit trail;
- the SSE hub for the console;
- the seed/reset hooks the demo engine depends on.

## Design

**Stack:** FastAPI + SQLModel on SQLite (`data/control/control.db`, WAL mode), Alembic-free (`SQLModel.metadata.create_all` + a tiny versioned migration list). OpenAPI must be clean and fully typed; C5 generates TS types from it.

**Schema (tables):**

| Table | Columns / notes |
|---|---|
| `users` | email, name, password_hash (argon2), role, tenant_id or `*` |
| `sessions` | Opaque token cookie, `HttpOnly`, `SameSite=Strict`; expiry `VEYRA_SESSION_TTL_MIN` |
| `tenants` | id, name, created_at |
| `sources` | id, tenant_id, name, vendor, zone, transport (`syslog_udp`, `syslog_tcp`, `http_push`), listener, match_kind, match_value, contract_id, expected_eps, salt_buckets, status |
| `api_keys` | key_id, source_id, tenant_id, secret_sha256, pepper_id, quota_eps, status, created_by, created_at, revoked_at |
| `contracts`, `contract_versions` | Used by C2; create them now with the columns C2 lists |
| `drafts`, `drift_items`, `replay_jobs` | Placeholders for C2–C4 |
| `audit` | Mirror of the IF-AUDIT topic for fast console reads |

**Roles and scoping:**
- `admin` (all), `pack_author` (own tenant: sources, drafts, submit), `pack_approver` (approve/promote, any tenant), `org_viewer` (read own tenant), `auditor` (read all + evidence).
- A dependency `require(role, tenant=…)` on every route. Tenant-scoped queries **always** filter by the session's tenant unless the user is platform (`*`). Add a test that an `org_viewer` of tenant A gets 404 (not 403) for tenant B's resources.

**Control publisher** (`publisher.py`):
- An idempotent producer, key per IF-CONTROL.
- On startup, run `republish_all()`: every source, active key, contract (with its candidate), vocab, enrich table and routes document. This makes the compacted topic always complete even after a reset.
- On every mutation, publish the changed key synchronously before returning 200 (flush with a timeout). Consumers must see the change within 1 s.

**Default vocab and enrich tables** are seeded from `services/control_api/seed/`:
- `status_words`: `FAILED|failure|denied → 2`, `OK|success|accepted → 1`;
- `severity_words`;
- `asset_inventory`: a small CSV of demo hosts with criticality;
- `zone_map`: CIDR → zone.

**Inventory writer.** On source create/update/delete, rewrite `edge/vector/inventory/sources.csv` atomically (tmp + rename) from all active syslog sources, then trigger the reload using the mechanism A1 verified (IF-INVENTORY). If A1 chose "restart the edge container", do it via the Docker API (demo profile), and log it.

**API keys.**
- The secret is `veyra_` + 32 base62 chars from `secrets`. Store `sha256(pepper || secret)`. The pepper is in `data/keys/api_pepper` (created at first boot, 0400, mounted read-only into the gateway).
- The response returns the secret **once**, plus `endpoints` and `curl_example` built from `services/ingest_gateway/API.md` (A2) and settings (`VEYRA_PUBLIC_HOST`).
- **Demo mode only:** remember the last issued `(key_id, secret, source_id)` in memory for `/internal/demo/last-key`.

**Audit.** Every mutation writes IF-AUDIT to SQLite and the `audit` topic: login, logout, source/key create/revoke, contract submit/approve/promote/rollback, draft create/edit, replay start, reset.

**SSE hub** (`GET /stream`): an asyncio broadcast. Publishes `source`, `contract`, `drift`, `draft` and `replay` events from mutations, plus `overview` ticks (the overview itself is computed by B's evidence-api; the console subscribes to both streams; C1 only relays control-side events). Heartbeat every 15 s.

**Seed** (`python -m control_api.seed --scenario sih_main`) creates:
- tenants `t_ntro_core` and `t_maha_power`;
- users `admin@veyra`, `author@maha`, `approver@veyra` (passwords from `VEYRA_DEMO_PASSWORD`, default `veyra-demo`);
- NTRO sources `src_fw_dmz_01` (dmz-tcp, `peer_ip` or `syslog_host fw-dmz-01`) and `src_lnx_core_07` (core-udp, `syslog_host core-lnx-07`);
- library contracts active for them (placeholder until C2/C3; import from `packages/veyra_engine/tests/contracts/`);
- the routes document (A6's defaults).

Note: the Maha Power auth server is **not** seeded; it is onboarded live, and its inventory row `syslog_host fw01 → src_authsrv_01` is created when it is onboarded. It only exists as a syslog row if the onboarding transport is syslog. For the demo (HTTP push) no inventory row is needed.

**Internal endpoints** (bound to the docker network; Caddy does not route `/internal`):
- `POST /internal/drift`: stub for C3.
- `POST /internal/reset`: wipe SQLite, `git reset --hard seed` in the contract registry (`VEYRA_CONTRACTS_REPO`, default `../contracts-repo`), re-seed, republish all, rewrite the inventory. Must finish in < 10 s.
- `GET /internal/demo/last-key`.

## Tasks
- [ ] 1. Service skeleton, settings, DB, migrations list, password hashing, sessions, role/tenant dependencies.
- [ ] 2. Tenants, sources, keys endpoints; tenant-isolation tests.
- [ ] 3. The control publisher + `republish_all` + vocab/enrich/routes seeding.
- [ ] 4. Inventory writer + reload trigger (coordinate with A1).
- [ ] 5. Audit (SQLite + topic) + `GET /audit`.
- [ ] 6. SSE hub + `/stream`.
- [ ] 7. Seed CLI + `/internal/reset` + `/internal/demo/last-key`.
- [ ] 8. Integration test: create source + key → the gateway (or a mock consumer) sees `apikey:` and `source:` on `control` within 1 s; revoke → tombstone/`status=revoked` within 1 s.

## Acceptance criteria
- [ ] AC1: `republish_all` after a fresh Kafka → the normalizer loads the library contracts and CP1 passes.
- [ ] AC2: Key issue → a HEC request with the key succeeds (with A2); revoke → 401 within 2 s.
- [ ] AC3: Tenant isolation tests pass (404 across tenants for non-platform users).
- [ ] AC4: `/internal/reset` < 10 s, and the state equals a fresh seed.
- [ ] AC5: Every mutation produces an audit row visible via `GET /audit`.

## Settings
`VEYRA_SESSION_TTL_MIN` (480), `VEYRA_DEMO_PASSWORD`, `VEYRA_PUBLIC_HOST`, `VEYRA_CONTROL_DB`, `VEYRA_DEMO_MODE`.

## Implementation notes
_(filled after execution)_
