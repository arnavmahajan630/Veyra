# A6 — Router, Wazuh integration, throughput bench

```
track: A   owner: A   status: done
contracts: v1.5
depends_on: [A3 (minimal part), A5 (revisions)]   unblocks: [CP1 (minimal), CP3, S2 numbers slide]
consumes: [IF-NORM-EVENT, IF-ROUTES, IF-CONTROL (routes), IF-KEYPROVIDER (hmac key), IF-WAZUH]
provides: [IF-RECEIPT, wazuh/ rules and config, bench reports]
directories: [services/router/, wazuh/, tools/bench/, compose/ (wazuh mounts only)]
```

## Goal

Declarative routing from `norm.*` to Wazuh (and a masked partner sink) with per-route filters, formats, PII masking, retries, circuit breakers and delivery receipts. Wazuh rules that make VEYRA events visible and let a brute-force alert fire on normalized fields. A throughput bench that produces honest scaling numbers.

**Do "minimal A6" (tasks 1–3 and 6) immediately after A3 for CP1.**

v1 refs: §10.1, ADR-05, §13 (SIEM outage). Demo beats 3–4.

## Design

### Router service
- Consumer `norm.*` (pattern), group `router`.
- Routes from `control` key `routes`; fallback `services/router/routes.default.yaml`.
- Each route has its own worker task with a bounded queue (backpressure isolates routes, mirroring v1's per-route consumer groups; document this simplification). Offsets are committed only when **every** route has flushed and receipted the record (at-least-once).
- **Filter:** tenants, tiers, classes, sources.
- **Format:**
  - `ocsf_json` = the event plus convenience fields `veyra.{tier,class,tenant,source,revision}`;
    <!-- synced from A3 --> all five come straight off `ulpf` (`tier`, `contract`, `tenant_id`,
    `source_id`, `revision`), which is complete on every event including tier 4. `lineage` rows also
    carry `search_terms` (observables plus user / IP / hostname) if the router ever needs them.
  - stretch: `cef`, `leef`.
- **Masking:**
  - `hmac` = HMAC-SHA256(route key, value), truncated to 16 hex, prefixed `h_`;
  - `redact` = `"[REDACTED]"`;
  - `tokenize` (stretch).
  - The masking key comes from `data/keys/route_hmac` (created by B's KeyProvider bootstrap or by the router itself on first boot; coordinate in the changelog).
- **Sinks:**
  - `ndjson_file`: buffered append, fsync every `fsync_ms`, rotate at `VEYRA_SINK_ROTATE_BYTES` (Wazuh follows the file name; test rotation);
    <!-- synced from A1 --> if you add a Vector-side sink anywhere, note that a templated topic
    disables its healthcheck, and a disk buffer has a 256 MiB + 32 B floor.
  - `syslog_tcp`: RFC 5425 octet-counting, reconnect with exponential backoff, circuit breaker (open after N failures, half-open probe);
  - `http_json`.
- **Receipts:** IF-RECEIPT per `(event, route)` to `receipts`: `delivered` after flush, `filtered` when the filter rejects, `failed` on permanent failure.
- **Metrics:**
  - `veyra_route_events_total{route,status}`
  - `veyra_route_lag_seconds{route}`
  - `veyra_route_breaker_state{route}`

### Wazuh
- `wazuh/ossec.conf.d/veyra_localfile.xml`: `<localfile><log_format>json</log_format><location>/sinks/wazuh/veyra.ndjson</location></localfile>`.
- `wazuh/rules/veyra_rules.xml`, ids 100100–100199 per IF-WAZUH:
  - 100100 is the parent (matches the `veyra.tier` field present), level 3, group `veyra`.
    <!-- synced from S0 --> **100100 itself must be a child of built-in rule 99000**
    (`<if_sid>99000</if_sid>`): 99000 "Amazon Security Lake rules grouped" is level 0 and matches any
    json event with `activity_id` and `category_uid`, i.e. every OCSF event, so a sibling rule never
    alerts. S0 ships 100100 in this shape already, verified with `wazuh-logtest`; add 100110-100130
    as children of 100100.
  - Children match JSON fields with `<field name="class_uid">^3002$</field>`, `<field name="status_id">^2$</field>`, `<field name="veyra.tier">^3$</field>`, and so on.
  - 100111 uses `frequency="5" timeframe="60"`, `<if_matched_sid>100110</if_matched_sid>` and `<same_field>src_endpoint.ip</same_field>`. **Verify `same_field` works with nested JSON field names on the pinned Wazuh version.** If not, add a flat convenience field `veyra.src_ip` and use that.
- Validate every rule with `wazuh-logtest`, using sample lines committed in `wazuh/tests/`.
- Dashboard saved objects: an index-pattern-based Discover search "VEYRA events" (`rule.groups: veyra`) and "VEYRA alerts ≥ 5", exported to `wazuh/dashboard/saved_objects.ndjson` for B7's reset to re-import.

### Throughput bench (`tools/bench/throughput.py`)
1. Pre-load N envelopes (a corpus mix: 60% tier 1, 30% tier 3, 10% tier 4) into `raw.bench`.
2. Start normalizers at replicas R ∈ {1, 2, 4, …} (up to the profile maximum) and measure sustained EPS until lag reaches 0.
3. The same for the router with the NDJSON sink.

Output: `reports/A6-bench-<machine>.md` with a table and the machine spec. These numbers go on the slide.

## Tasks
- [x] 1. (minimal) The router with the `wazuh_main` NDJSON route + receipts.
  <!-- synced from A6 --> Nine modules under `services/router/src/router/`: `__main__`,
  `delivery`, `filters`, `formats`, `keys`, `masking`, `routes`, `settings`, `sinks`. Each route
  is compiled once at load — bad sink type, unknown masking mode or misspelled filter key are
  refused at load, not per-event. `RouteWorker` owns one route's bounded queue and thread;
  `Dispatcher` fans one event to every route and waits for all of them before the offset commits
  (at-least-once). Fallback routes in `services/router/routes.default.yaml`; live routes from the
  `control` topic key `routes` via `ControlReader`.
- [x] 2. (minimal) Wazuh localfile config + rule 100100; confirmed visible in Discover.
  <!-- synced from S0 --> Done in S0: `wazuh/ossec.conf.d/veyra_localfile.xml`, rule 100100, and
  `wazuh/entrypoint-veyra.sh` (which copies the rules in on every start, since `/var/ossec/etc` is a
  named volume). A probe line reaches `wazuh-alerts-*` under rule 100100. Run `make wazuh-init` once
  after `make wazuh-certs`, or the indexer never leaves "Security not initialized".
- [x] 3. (minimal) Compose mounts for `/sinks/wazuh`.
  <!-- synced from S0 --> `data/sinks/wazuh` is mounted into the manager at `/sinks/wazuh`, and the
  router service already mounts `data/sinks` at `/sinks`. `make up` creates
  `data/sinks/wazuh/veyra.ndjson` as the invoking user — nothing in the manager container may create
  it, or the router (uid 10001) cannot append.
- [x] 4. Full routes: filters, masking, the partner route, syslog_tcp with breaker, http_json.
  <!-- synced from A5 --> `shadow` is a live topic now and must **not** be routed anywhere: it is a
  candidate-vs-active comparison, not an event. A replayed event, on the other hand, *is* routed —
  it carries `ulpf.revision >= 2` and `ulpf.supersedes`, so a route that does not account for it will
  deliver the same event to Wazuh twice under different revisions. Decide deliberately whether the
  router suppresses superseded revisions or lets the sink see both.
  <!-- synced from A6 --> Decision: the router delivers **both** revisions. A replayed event is a
  correction (a new contract normalised something that was previously unknown), and rule 100130
  makes the correction visible to an analyst. Suppressing revision 1 would erase the audit trail;
  the sink sees two records for the same original event, which is correct behaviour. Routes are
  consumed from `^norm\..*` (never `shadow`): the consumer group pattern enforces this at the
  broker, not just by convention. `filters.py`, `masking.py`, `sinks.py` (all three sink types),
  `routes.default.yaml` with `wazuh_main` + `partner_masked` implement task 4 in full.
- [x] 5. Full rules 100110–100130 + logtest samples; brute force verified.
  <!-- synced from A4 --> Tier-3 events now carry `ulpf.class_hint` and a `severity_id` derived from a
  word vocabulary (`derived_fields["severity_id"] = "vocab:severity_words"`). Both are **hints**: the
  word match reads "failed to disable alerting" as a failure. No rule that pages a human should fire on
  tier-3 severity or on `class_hint` alone — match on `ulpf.tier` plus the located fields, and keep
  tier-3 rules informational. Observable names to key on for an uncategorized event: `ip_1`, `ip_2`,
  `user`, `host_1`.
  <!-- synced from A6 --> Two findings established with `wazuh-logtest` on 4.14.8 rather than assumed:
  (1) `same_field` on a nested path (`src_endpoint.ip`) is unreliable on this version; the flat field
  `veyra.src_ip` is written by the router's `ocsf_json` formatter and used in rule 100111 instead.
  (2) The default field matcher is OS_Regex with no alternation: `^([2-9]|\d{2,})$` does not load;
  `type="pcre2"` is required for rule 100130's revision check. Both would have been invisible without
  running the real engine: the first silently produces the wrong alert, the second stops analysisd
  loading the file entirely. Five sample files in `wazuh/tests/`; `make wazuh-rules-test` pipes each
  through the manager's own engine and asserts the expected rule id fires.
- [x] 6. (minimal) Integration test: event → sink line → Wazuh API `GET /alerts`-style query or indexer search finds it (automated, via the indexer REST API).
  <!-- synced from A6 --> `tests/int/test_router.py` publishes norm events to `norm.*`, the running
  router writes them to the NDJSON sink, and `tests/int/test_wazuh.py` polls the indexer REST API
  (`https://localhost:9200/wazuh-alerts-*/_search`) until the alert appears or a 90-second deadline
  passes. `data.message` is a keyword field in the wazuh-alerts template, so the test uses an exact
  term query on a UUID-suffix marker to avoid matching unrelated alerts.
- [x] 7. Saved objects export; `WAZUH=remote` mode (route sink switches to syslog_tcp to `VEYRA_WAZUH_REMOTE_HOST`; the remote manager has a `<remote>` syslog config and the same rules; document it in `wazuh/REMOTE.md`).
  <!-- synced from A6 --> `wazuh/dashboard/saved_objects.ndjson` holds two Discover searches
  (`veyra-events`, `veyra-alerts-level5`) plus the `wazuh-alerts-*` index pattern, exported with
  `includeReferencesDeep`. Fixed ids prevent re-export from creating a second copy. B7 imports this
  file after re-creating the indices. `wazuh/REMOTE.md` documents the `WAZUH=remote` invocation,
  the RFC 5425 framing rationale, the remote manager's `<remote>` config block, and the operational
  behaviour during an outage (offsets held, breaker visible in metrics, no loss within retention).
- [x] 8. Throughput bench + report (laptop now; workstation if procured).
  <!-- synced from A5 --> Bench one pass with a **canary attached** to the busiest source: shadow mode
  doubles the engine work for that source, so that is the realistic worst case now, and
  `veyra_shadow_skipped_total` says whether the candidate kept up or was being dropped.
  <!-- synced from A2 --> There are two ingestion paths to bench now, and they cost different things:
  the edge (Vector, syslog) and the gateway (HTTP, per-request `flush` with an ack timeout). The
  gateway's ceiling is dominated by how many events a request carries, since durability is paid per
  request — bench it with realistic batch sizes rather than one event per POST, and say which was used.
  <!-- synced from A6 --> `tools/bench/throughput.py` implements both legs. Mix is 60% tier 1 /
  30% tier 3 / 10% tier 4 (not all tier 1 — a bench that only measures the fast path flatters
  itself). Normalizer leg uses host worker processes in one consumer group (not `--scale`, which
  `container_name` pins prevent). Router leg publishes to `norm.*` and polls the sink file size
  until stable. `make bench-throughput COUNT=20000 REPLICAS=1,2,4` runs the full curve.
  Report written to `docs/plan/reports/A6-bench-<host>.md`.

## Acceptance criteria
- [x] AC1: 6 tier-1 sshd failures from the same IP within 60 s → alert 100111 in Wazuh (human looks + indexer query).
- [x] AC2: Replayed revision 2 events → 100130 visible; for authsrv T3 ×8 replayed as tier 1 → 100111 fires for `103.21.4.77`.
- [x] AC3: The partner sink contains the Maha Power auth events with `user.name` HMAC'd and `raw_data` redacted; NTRO events are absent (filter).
- [x] AC4: Wazuh stopped for 2 min → no data loss after restart; receipts eventually `delivered`; breaker state visible in metrics (syslog_tcp mode).
- [x] AC5: The bench report exists with measured numbers.

## Settings
`VEYRA_SINK_ROTATE_BYTES`, `VEYRA_ROUTE_QUEUE_MAX`, `VEYRA_ROUTE_BREAKER_FAILS`, `VEYRA_WAZUH_REMOTE_HOST`, `VEYRA_WAZUH_REMOTE_PORT`.

## Implementation notes

**Router architecture.** Nine modules under `services/router/src/router/`. Each route is compiled
once at load — bad sink type, unknown masking mode or misspelled filter key are refused at load,
not per-event, so a typo cannot silently leak one tenant's events to another's partner feed.
`RouteWorker` owns one route's bounded queue and thread; `Dispatcher` fans one event to every route
and waits for all of them before the Kafka offset commits (at-least-once across a crash).

**control.py bug fix.** `ControlReader._handle()` now returns `bool` and `on_change()` fires only
for keys this reader subscribed to (the `prefixes` filter). Without this the router reinstalled its
whole route set — aborting in-flight deliveries — on every `apikey.*` publish by control-api.

**Route key.** `data/keys/route_hmac` (32 random bytes, mode 0400, created on first boot). Each
route derives `HMAC(master, route_id)` as its own subkey, so two partners cannot join their feeds:
the same user is a different `h_…` on each feed. Masking sweeps the replaced value through every
other field in the payload (message, observables, any string), so masking `user.name` also removes
the identity from `message: "user=a.sharma FAILED…"`. A6's integration test caught exactly that
gap — one partner line had `user.name: h_…` next to the unmasked message string.

**File rotation.** `NdjsonFileSink._rotate()` copies the content aside and truncates in place,
never renames the inode. Wazuh's `<localfile>` holds the fd open; renaming it would leave the
manager reading a file nothing writes to any more — no error, no alerts, until a restart.

**Wazuh rule ordering.** Children are tried in document order and the first match wins. 100111
(brute force) comes before 100130 (corrected revision) so a replayed attack alerts as an attack,
not a footnote. A correction that is not also an attack trips 100130 because 100111 did not match.

**`same_field` finding.** Tested on 4.14.8 with `wazuh-logtest`: `same_field` on a dotted nested
path (`src_endpoint.ip`) is not reliable on this version. The router's `ocsf_json` formatter always
writes `veyra.src_ip` (a flat field) and rule 100111 correlates on that. The nested value is still
in the event for any analyst reading it.

**`pcre2` finding.** The default field matcher is OS_Regex with no alternation. The rule
`<field name="veyra.revision">^([2-9]|\d{2,})$</field>` stops analysisd loading the rules file
entirely. `type="pcre2"` is required for rule 100130's "revision ≥ 2" check.

<!-- synced from S0 --> Wazuh is 4.14.8, single node, credentials at the image defaults
(admin/admin) until S2 rotates them; the indexer is reachable from the host at
`https://localhost:9200` and the dashboard at `https://localhost:8443`. Expect the
alerts.json → filebeat → indexer hop to take tens of seconds on the laptop, so any assertion about
"appears in Wazuh" needs a generous wait. Never `pkill filebeat` inside the manager: restart the
container instead (S0 wasted a round on a stuck filebeat registry).

