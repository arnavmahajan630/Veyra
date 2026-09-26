# A6 — Router, Wazuh integration, throughput bench

```
track: A   owner: A   status: todo
contracts: v1.0
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
  - stretch: `cef`, `leef`.
- **Masking:**
  - `hmac` = HMAC-SHA256(route key, value), truncated to 16 hex, prefixed `h_`;
  - `redact` = `"[REDACTED]"`;
  - `tokenize` (stretch).
  - The masking key comes from `data/keys/route_hmac` (created by B's KeyProvider bootstrap or by the router itself on first boot; coordinate in the changelog).
- **Sinks:**
  - `ndjson_file`: buffered append, fsync every `fsync_ms`, rotate at `VEYRA_SINK_ROTATE_BYTES` (Wazuh follows the file name; test rotation);
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
- [ ] 1. (minimal) The router with the `wazuh_main` NDJSON route + receipts.
- [ ] 2. (minimal) Wazuh localfile config + rule 100100; confirmed visible in Discover.
- [ ] 3. (minimal) Compose mounts for `/sinks/wazuh`.
- [ ] 4. Full routes: filters, masking, the partner route, syslog_tcp with breaker, http_json.
- [ ] 5. Full rules 100110–100130 + logtest samples; brute force verified.
- [ ] 6. (minimal) Integration test: event → sink line → Wazuh API `GET /alerts`-style query or indexer search finds it (automated, via the indexer REST API).
- [ ] 7. Saved objects export; `WAZUH=remote` mode (route sink switches to syslog_tcp to `VEYRA_WAZUH_REMOTE_HOST`; the remote manager has a `<remote>` syslog config and the same rules; document it in `wazuh/REMOTE.md`).
- [ ] 8. Throughput bench + report (laptop now; workstation if procured).

## Acceptance criteria
- [ ] AC1: 6 tier-1 sshd failures from the same IP within 60 s → alert 100111 in Wazuh (human looks + indexer query).
- [ ] AC2: Replayed revision 2 events → 100130 visible; for authsrv T3 ×8 replayed as tier 1 → 100111 fires for `103.21.4.77`.
- [ ] AC3: The partner sink contains the Maha Power auth events with `user.name` HMAC'd and `raw_data` redacted; NTRO events are absent (filter).
- [ ] AC4: Wazuh stopped for 2 min → no data loss after restart; receipts eventually `delivered`; breaker state visible in metrics (syslog_tcp mode).
- [ ] AC5: The bench report exists with measured numbers.

## Settings
`VEYRA_SINK_ROTATE_BYTES`, `VEYRA_ROUTE_QUEUE_MAX`, `VEYRA_ROUTE_BREAKER_FAILS`, `VEYRA_WAZUH_REMOTE_HOST`, `VEYRA_WAZUH_REMOTE_PORT`.

## Implementation notes
_(filled after execution)_
