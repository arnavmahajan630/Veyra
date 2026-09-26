# A2 — Ingest gateway: HEC-compatible push, per-source API keys, quotas, batch

```
track: A   owner: A   status: todo
contracts: v1.1
depends_on: [S0, A1 (envelope parity vectors)]      unblocks: [CP3, Beat 2–3]
consumes: [IF-CONTROL (apikey:*, source:*), IF-TOPICS, IF-NAMING]
provides: [IF-ENVELOPE (gateway producer)]
directories: [services/ingest_gateway/]
```

## Goal

An HTTP ingestion service that any existing shipper can target (Splunk-HEC-compatible). It authenticates each request with a per-source API key issued at onboarding, stamps envelopes identically to the edge, enforces per-source quotas, supports post-hoc batch uploads with a custody flag, and acknowledges only after Kafka has durably accepted the events.

This is the "org gets an API doc + key" part of the onboarding story (Beat 2) and the path the Maha Power auth server uses in Beat 3.

## Design

**Endpoints** (FastAPI + uvicorn, port 8088):

| Endpoint | Behaviour |
|---|---|
| `POST /services/collector/raw` | HEC raw. The body is text; newline-split with the same continuation rule as A1 (put `framing.split_lines()` in `veyra_common.framing` so both paths share it). `transport=http_hec_raw`. |
| `POST /services/collector/event` | HEC event. The body is one or more concatenated JSON objects `{"event": <string or object>, "time": <epoch>, "host":…, "source":…, "sourcetype":…}`. The raw bytes are the `event` string (if an object: its compact JSON serialization, recorded in `framing.method="http_body"`). The HEC `time` goes to `unmapped` via the envelope `hec_meta` (additive field; log it in the plan sync). |
| `POST /v1/batch` | Multipart file. Each line is an event with `custody=post_hoc` and `framing.method=batch_line`. The response includes a manifest `{count, sha256_of_file, first_event_uid, last_event_uid}`. **Stretch:** header `X-Veyra-Batch-Signature` (Ed25519 over the manifest with a source-registered public key) sets `custody=post_hoc_signed`. |
| `GET /healthz`, `GET /metrics` | Standard. |

**Auth.**
- Accept `Authorization: Splunk <secret>` or `Bearer <secret>`.
- Look the key up in an in-memory registry built from the compacted `control` topic (`apikey:*`, `source:*`), and compare `sha256(pepper || secret)` in constant time.
- Unknown or revoked → 401 with an HEC-style JSON error.
- `auth.method=api_key` and `auth.key_id` go into the envelope.

**The pepper.** It is shared with C1 via the `data/keys/api_pepper` file, mounted read-only. C1 generates it at first boot, and the gateway waits for the file.

**Quotas.** A token bucket per source (`quota_eps` from the key or source record; default `VEYRA_GATEWAY_DEFAULT_QUOTA_EPS`). When exceeded, return 429 with `Retry-After`. Exposed in metrics.

**Durability.**
- Produce all events of a request asynchronously, then `flush()` with timeout `VEYRA_GATEWAY_ACK_TIMEOUT_MS`.
- Return 200 `{"text":"Success","code":0,"ackId":n}` **only if every delivery callback succeeded**. Otherwise return 503 with no partial ack. The client retries; duplicates are acceptable (at-least-once) and dedupe by `event_uid` is impossible here, which is documented.

**Size cap.** Events over `VEYRA_MAX_EVENT_BYTES` are truncated with `framing.truncated=true`. Bodies over `VEYRA_GATEWAY_MAX_BODY_BYTES` → 413.

**Stamping.** Reuse `veyra_common.envelope.stamp(raw_bytes, **meta)`. It must reproduce A1's parity vectors exactly.

## Tasks
- [ ] 1. Service skeleton on `ServiceApp`; control-topic follower building the key and source registries (with a mock publisher script until C1 lands: `tools/mock_control_publish.py`).
- [ ] 2. `veyra_common.framing` (shared with A1's rule, pure Python) and `veyra_common.envelope.stamp`; parity tests against the A1 vectors.
- [ ] 3. The three endpoints with auth, quotas and durable acks.
- [ ] 4. Client compatibility test: configure a throwaway Vector with a `splunk_hec_logs` sink pointed at the gateway, and confirm events arrive. This proves "point your existing shipper here".
- [ ] 5. Integration tests: auth ok, bad key 401, revoked key 401 within 2 s of revocation, quota 429, multi-line HEC event, batch custody.
- [ ] 6. Write the API doc snippet (`services/ingest_gateway/API.md`) that C1 embeds in the key card: curl examples for raw, event and batch.

## Acceptance criteria
- [ ] AC1: `curl` with a valid key posts the T3 multi-line event → one envelope on `raw.custom`, with `source_id=src_authsrv_01`, `tenant_id=t_maha_power`, `auth.method=api_key`.
- [ ] AC2: Revoking the key in control → 401 within 2 s.
- [ ] AC3: A quota of 5 EPS, then sending 50 in 1 s → about 5 accepted and the rest 429; the metrics show it.
- [ ] AC4: A Vector `splunk_hec_logs` client delivers 1000 events → 1000 envelopes.
- [ ] AC5: With Kafka down, requests return 503 (never 200) and nothing is lost after retry.

## Settings
`VEYRA_GATEWAY_DEFAULT_QUOTA_EPS`, `VEYRA_GATEWAY_ACK_TIMEOUT_MS` (5000), `VEYRA_GATEWAY_MAX_BODY_BYTES` (10 MB).

## Risks
- **HEC quirks** (concatenated JSON objects without separators): implement a streaming JSON object splitter and test it with Vector's actual output.

## Stretch
- OTLP/HTTP JSON logs at `/v1/logs`.
- Signed batch manifests.

## Implementation notes
_(filled after execution)_
