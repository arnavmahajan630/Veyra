# VEYRA ingest API

Send logs over HTTP with the API key issued when your source was onboarded. The endpoints are
**Splunk-HEC compatible**, so any shipper that can talk to a Splunk HEC endpoint — Vector, Fluent
Bit, Filebeat, Logstash, curl — works without a custom integration.

- Base URL: `http://<veyra-host>:8088`
- Authentication: `Authorization: Splunk <secret>` (`Bearer <secret>` also accepted)
- Your key is shown **once**, at issue time. VEYRA stores only `sha256(pepper + secret)`.

## POST /services/collector/event — JSON events

One or more JSON objects. Separators are optional, so both NDJSON and Splunk's back-to-back style
work.

```bash
curl -s http://localhost:8088/services/collector/event \
  -H "Authorization: Splunk $VEYRA_KEY" \
  -d '{"event":"<134>Sep 26 14:05:11 fw01 app[233]: user=a.sharma FAILED login","time":1790000000,"host":"authsrv-01"}'
```

```json
{"text": "Success", "code": 0, "ackId": 41, "events": 1}
```

- `event` may be a string (kept **exactly** as the bytes you sent) or an object (stored as its
  compact, key-sorted JSON, so the same object always produces the same hash).
- `time`, `host`, `source`, `sourcetype` and `index` are preserved as the **sender's claims** and
  surfaced under `unmapped.hec_meta`. They never overwrite what VEYRA established itself: the receipt
  time is when VEYRA read the bytes, and the event time is derived from the log line.

## POST /services/collector/raw — plain text

The body is text. Lines are split the same way the syslog collectors split them, including the
continuation rule: a line starting with whitespace or `at ` belongs to the event above it, so a Java
stack trace stays one event.

```bash
curl -s http://localhost:8088/services/collector/raw \
  -H "Authorization: Splunk $VEYRA_KEY" \
  --data-binary @/var/log/auth.log
```

## POST /v1/batch — historical upload

A multipart file upload for logs that are not live. Every line is one event, marked
`custody=post_hoc`, so an analyst can always tell a back-filled record from one VEYRA saw in real
time.

```bash
curl -s http://localhost:8088/v1/batch \
  -H "Authorization: Splunk $VEYRA_KEY" \
  -F "file=@/backups/auth-2026-09-01.log"
```

```json
{
  "text": "Success",
  "code": 0,
  "ackId": 42,
  "manifest": {
    "count": 18432,
    "sha256_of_file": "9f2c…",
    "filename": "auth-2026-09-01.log",
    "first_event_uid": "0192a4f0-0000-7000-8000-000000000001",
    "last_event_uid": "0192a4f0-0000-7000-8000-000000004801",
    "custody": "post_hoc"
  }
}
```

Keep the manifest. `sha256_of_file` is over the bytes you uploaded, so it proves later which file
this batch was, and the two `event_uid`s bound the range in VEYRA's lineage index.

## Responses

| Status | Body | What to do |
|---|---|---|
| 200 | `{"text":"Success","code":0,…}` | The events are durably stored. Safe to drop your copy. |
| 400 | `{"text":"…","code":6}` | The body is not valid HEC. The message says where. Do not retry unchanged. |
| 401 | `{"text":"Invalid authorization","code":3}` | Key missing, unknown or revoked. Do not retry; get a new key. |
| 413 | `{"text":"Request body exceeds N bytes","code":6}` | Send smaller batches. |
| 429 | `{"text":"Source … is over its quota …","code":9}` + `Retry-After` | Slow down and retry after the given delay. |
| 503 | `{"text":"Not durably accepted: …","code":9}` | VEYRA could not store the events. **Retry the whole request.** |

Two properties worth designing your shipper around:

1. **A 200 means durable.** The gateway acknowledges only after the bus has accepted every event in
   the request. It never partially acknowledges: if anything failed, you get 503 and the whole
   request is yours to retry.
2. **Delivery is at-least-once.** A retry after a 503 that actually succeeded will store the same
   bytes twice, under two different `event_uid`s, and nothing deduplicates them — HEC has no
   idempotency key to key on. Lineage keeps both, and their identical `raw_sha256` is what shows
   they are the same bytes.

## Limits

| Limit | Default | Effect when exceeded |
|---|---|---|
| Request body | 10 MB | 413 |
| One event | 64 KB | Stored truncated, flagged `framing.truncated`; the hash covers what was stored |
| Rate | your source's `quota_eps` | 429 with `Retry-After` |

Quotas are per source and hold roughly one second of burst, so a source rated at 50 EPS can send 50
at once and then has to keep to its rate.

## Pointing an existing shipper here

Vector, as an example — no VEYRA-specific configuration, just its Splunk HEC sink:

```toml
[sinks.veyra]
type = "splunk_hec_logs"
inputs = ["my_source"]
endpoint = "http://veyra-host:8088"
default_token = "${VEYRA_KEY}"
encoding.codec = "json"
```
