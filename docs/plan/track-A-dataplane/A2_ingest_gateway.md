# A2 — Ingest gateway: HEC-compatible push, per-source API keys, quotas, batch

```
track: A   owner: A   status: done
contracts: v1.4
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
- [x] 1. Service skeleton on `ServiceApp`; control-topic follower building the key and source registries (with a mock publisher script until C1 lands: `tools/mock_control_publish.py`).
- [x] 2. `veyra_common.framing` (shared with A1's rule, pure Python) and `veyra_common.envelope.stamp`; parity tests against the A1 vectors.
  <!-- synced from A1 --> Both already exist and are already the shared contract with the edge:
  `tests/int/test_edge.py::test_edge_and_stamp_agree_on_the_same_bytes` and
  `::test_parity_vectors_from_s0_still_hold` lock the VRL and the Python together. Reuse
  `framing.split_lines` for the HEC raw endpoint instead of re-implementing the continuation rule,
  and run those two tests after touching either side.
- [x] 3. The three endpoints with auth, quotas and durable acks.
- [x] 4. Client compatibility test: configure a throwaway Vector with a `splunk_hec_logs` sink pointed at the gateway, and confirm events arrive. This proves "point your existing shipper here".
- [x] 5. Integration tests: auth ok, bad key 401, revoked key 401 within 2 s of revocation, quota 429, multi-line HEC event, batch custody.
- [x] 6. Write the API doc snippet (`services/ingest_gateway/API.md`) that C1 embeds in the key card: curl examples for raw, event and batch.

## Acceptance criteria
- [x] AC1: `curl` with a valid key posts the T3 multi-line event → one envelope on `raw.custom`, with `source_id=src_authsrv_01`, `tenant_id=t_maha_power`, `auth.method=api_key`.
- [x] AC2: Revoking the key in control → 401 within 2 s.
- [x] AC3: A quota of 5 EPS, then sending 50 in 1 s → about 5 accepted and the rest 429; the metrics show it.
- [x] AC4: A Vector `splunk_hec_logs` client delivers 1000 events → 1000 envelopes.
- [x] AC5: With Kafka down, requests return 503 (never 200) and nothing is lost after retry.

## Settings
`VEYRA_GATEWAY_DEFAULT_QUOTA_EPS`, `VEYRA_GATEWAY_ACK_TIMEOUT_MS` (5000), `VEYRA_GATEWAY_MAX_BODY_BYTES` (10 MB).

## Risks
- **HEC quirks** (concatenated JSON objects without separators): implement a streaming JSON object splitter and test it with Vector's actual output.

## Stretch
- OTLP/HTTP JSON logs at `/v1/logs`.
- Signed batch manifests.

## Implementation notes

Executed 2026-09-28 by Person A. Report: [A2.md](../reports/A2.md). All 6 tasks and all 5 ACs pass.

**What was reused rather than rebuilt.** `veyra_common.envelope.stamp` and
`veyra_common.framing.split_lines` are the same functions the edge's VRL is locked against, so the
raw endpoint's multi-line handling is A1's rule by construction, not by imitation —
`tests/test_parity.py` re-derives S0's `cef_http_hec_event` and `t3_multiline_syslog_tcp` vectors
through the HTTP path and compares `raw_b64`/`raw_sha256` byte for byte. C1 already had the key half:
`secret_digest` is `sha256(pepper + secret)`, the pepper file is `data/keys/api_pepper`, and
**revocation republishes the key with `status="revoked"` rather than tombstoning it**, which is what
makes AC2 a matter of following the topic.

**The control follower now lives in `veyra_common.control.ControlReader`.** The gateway is the third
consumer of `control` (A6's router will be the fourth), and the part worth having exactly one copy of
is the readiness rule: wait until every assigned partition reaches the high watermark seen at
startup, because "poll returned nothing" usually means "not assigned yet". A3's control tests pass
unmodified, which is the evidence this was a move and not a rewrite.

**Auth is a digest lookup, not a scan.** The secret is hashed once with the pepper and the result is a
dict key, so `hmac.compare_digest` runs once on a fixed-length digest instead of the comparison cost
growing with the number of issued keys. Re-issuing a key under the same `key_id` drops the old digest
first, so a rotated secret cannot keep working — that case has its own test, because it is the one a
naive implementation gets wrong.

**Two states that are not 401.** A request arriving before the control backlog is read gets **503**,
not 401: the gateway does not yet know whether the key is good, and a client that treats 401 as fatal
would stop retrying a perfectly valid key. A key whose source record has not arrived yet is
**accepted** with `vendor`/`zone` defaulted (and a warning), because onboarding issues the key before
the source row lands and dropping the event would break P2.

**HEC's real quirk is the body format.** Clients concatenate JSON objects with no separator at all
(`{"event":"a"}{"event":"b"}`), which is neither JSON nor NDJSON. The split uses
`json.JSONDecoder().raw_decode` walking the index rather than brace counting, so a brace inside a
string cannot silently truncate the batch — a failure that would have been invisible, since the
client would still have received its 200.

**Durability.** Every message carries a delivery callback; the response is 200 only when all of them
succeeded within `VEYRA_GATEWAY_ACK_TIMEOUT_MS`. A broker error, a timeout, or a full local queue is
503 with no partial ack. The at-least-once consequence (a retry duplicates bytes under a new
`event_uid`, and nothing deduplicates) is documented in `API.md` as a property, not a footnote.

**Quotas cost events, not requests.** A 10-event request against a 5 EPS quota is 10 events of
budget, and a request that does not fit is refused whole — a request is never partially accepted. A
request larger than the whole bucket (a 40-line batch at 5 EPS) is allowed once the bucket is full and
drains it, or it could never be sent at all. One float detail earned a constant: refill is
`elapsed * rate`, and 0.4 s at 5 EPS computes to 1.9999999999998863, so an exact comparison would 429
a source sending exactly its quota — `EPSILON = 1e-9` fixes it.

**Deployment detail that would have looked like a bug.** control-api writes the pepper mode 0400 as
the invoking user, so `ingest-gateway` must run as that same uid (`user: "${VEYRA_UID}:${VEYRA_GID}"`
in compose, like control-api and immudb). `pepper.py` distinguishes "not there yet" (wait) from
"cannot read it" (fail fast with the fix in the message), because from the outside both look like
every key being wrong.

**Out of scope by decision:** both Stretch items (Ed25519-signed batch manifests → `post_hoc_signed`,
and OTLP/HTTP `/v1/logs`). Signed manifests need somewhere to register a source's public key, which is
IF-CONTROL and therefore C's; nothing in the demo script sends OTLP.
