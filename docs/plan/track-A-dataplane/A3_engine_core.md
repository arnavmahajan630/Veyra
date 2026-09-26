# A3 — Engine core + normalizer service (tiers 1, 2, 4; peeling; transactions)

```
track: A   owner: A   status: todo
contracts: v1.1
depends_on: [S0]                  unblocks: [CP1, A4, A5, A6, C2 (golden tests), B1]
consumes: [IF-ENVELOPE, IF-CONTROL (contract:*, vocab:*, enrich:*), IF-CONTRACT-COMPILED, IF-OCSF-SUBSET, IF-TOPICS]
provides: [IF-ENGINE-LIB, IF-NORM-EVENT, IF-ULPF, IF-LINEAGE, IF-DLQ, IF-TEMPLATE-SIG]
directories: [packages/veyra_engine/, services/normalizer/]
```

## Goal

Replace the S0 stub with the real deterministic engine (a pure library), and run it in a transactional Kafka service. By the end of A3:
- a registered source with an active contract produces tier 1 OCSF;
- an envelope that parses but misses required fields produces tier 2;
- anything else produces tier 4, raw-wrapped.

A4 adds the tier 3 extractor.

v1 refs: §9 (all nine stages), §9.1, §7.3 (transactions), ADR-02/03/04. Demo beats 3–5.

## Design: `veyra_engine`

**Pipeline:** `normalize(env)`:

```
decode → resolve contract → peel (envelope layers) → select text field → template match
      → map → time → enrich → pii → validate → tier decision → build OCSF + ulpf → DLQ record (if tier ≥ 2)
```

### Decode (`decode.py`)
- Try UTF-8 strict first. Otherwise use `charset-normalizer` best guess. Otherwise decode UTF-8 with `errors="replace"` and count invalid bytes.
- Build a **char→byte offset map** (a prefix array: byte length of each char in the source encoding). All spans are computed on text and converted to **byte offsets into the raw bytes** at the end. This is what makes Hindi or other multi-byte text safe for highlighting.

### Peel (`peel/`)
Each layer is a class with `detect(text, span)` and `parse(text, span) -> Fields`. A Field has `path`, `value`, `char_span | None` and `layer`. Layers:
- **`syslog`:** RFC 5424 and 3164 regexes (RE2). Fields: `syslog.pri`, `facility`, `severity`, `timestamp`, `host`, `app`, `pid`, `msgid`, `sd.*`, and body span.
- **`json`:** a **span-tracking JSON scanner** (`spanjson.py`, own implementation). It parses the first complete JSON object or array starting at the first non-space char of the body and records a char span for every scalar value, with paths like `json.msg` and `json.a.b[0]`. Text after the object becomes `json.__rest` with its span; A4 kv-parses it. String values are unescaped, but spans point at the raw (escaped) content, and `field_offsets` then map to the exact bytes shown in the raw.
- **`kv`:** `key=value` with quoted values and configurable separators; spans for each value.
- **`cef`:** pipe-escaped header plus extension kv, with spans. **`leef`:** tab-separated kv, with spans.
- **`csv`:** delimiter and header from the contract.
- **`regex`:** a user-provided RE2 with named groups.
- **`base64`:** decodes a field; the inner fields get `char_span=None`, and `derived` is `base64`.
- **Depth limit:** `VEYRA_PEEL_MAX_DEPTH`.

### Template match (`template.py`)
Consumes IF-CONTRACT-COMPILED `templates[].regex` (RE2, anchored), tries them in order on the text field, and takes the **first** match. Capture spans are offset by the text-field span. Also computes `template_sig(scope, text)` for every event.

### Map (`mapping.py`)
- Applies `map` entries: `capture | field | const | vocab | ts | text`.
- Typed coercion: `ip` validated via the `ipaddress` module; `int`; string.
- Writes nested OCSF paths.
- Records `field_offsets[ocsf_path] = byte_span` for `capture`, `field` and `text`, and `derived_fields[path] = kind` for the others.
- Builds `observables[]` from typed fields: IP → type_id 2, user → 4, hostname → 1. Verify the ids against the pinned OCSF.
- Collects `unmapped` from declared unmapped captures plus all unconsumed peeled fields (never lose data, v1 requirement C).

### Time (`timeparse.py`)
- Formats from the contract, else a small auto list.
- Timezone from the contract (zoneinfo), else UTC plus `tz_assumed`.
- Year inference: choose the year such that `event_time ≤ received_time + 1 day`.
- `clock_skew_ms = received - event`.
- If no time is found, use `received_time` with `time.source="received"`.
- Purity: no `datetime.now()`; use `envelope.received_time` only.

### Enrich (`enrich.py`)
Offline tables from `control` (`enrich:asset_inventory`, `enrich:zone_map`). Results are appended to the OCSF `enrichments[]` (name, value, data, provider), so enrichment never masquerades as source data (P4).

### Validate (`validate.py`)
- The vendored OCSF subset JSON Schemas (IF-OCSF-SUBSET) via `jsonschema`, with **compiled validators cached per class**.
- Contract `required` paths must be present.

### Tier decision

| Condition | Tier | conformance |
|---|---|---|
| Template matched, required present, schema valid | 1 | `match` |
| Template matched but required missing or schema invalid, **or** peel succeeded with no template but the contract exists | 2 | `partial` |
| No contract / no template / generic extraction path | 3 | `unknown_template` — A3 temporarily emits these as tier 4 until A4 lands; keep the code path |
| Decode failure, empty, guard tripped, exception | 4 | `unparseable` |

Tier 2 keeps the mapped fields and puts the rest in `unmapped`.

### Build (`build.py`)
- OCSF `metadata`, `class_uid`, `category_uid`, `type_uid`, `activity_id`, `time`, `message`, `raw_data` (decoded text), `unmapped`, `observables`.
- `ulpf` complete per IF-ULPF: `parse_path`, `contract`, `template`, tier, offsets, `derived_fields`, time, encoding, `engine_version`.
- **Deterministic serialization:** `veyra_engine.serialize(event)` uses sorted keys, compact separators and no floats for time (epoch ms int).

### DLQ record
For tier ≥ 2 only. `reason_code` per IF-DLQ. `text_masked` comes from the `veyra_engine.mask` module, which A4 finalizes; A3 does a simple version that masks values of keys matching `pass|pwd|token|secret|key|session|cookie` and emails.

## Design: `services/normalizer`

- Consumer: pattern `^raw\..*` plus `replay.raw`, group `normalizer`. Uses `veyra_common.kafka.TxnProcessor`, with batch size and time from settings.
- Control follower thread: reads `control` from the beginning; builds the compiled set, vocab and enrich tables; calls `engine.load(...)` atomically (copy-on-write swap). **Readiness is not signalled until the first full read of `control` completes.**
- Per record: `engine.normalize` → produce `norm.<category>` (key `event_uid`), `lineage` (key `event_uid`), `dlq` (if any), then send offsets, all in one transaction.
- Metrics:
  - `veyra_norm_events_total{tier,source}`
  - `veyra_norm_latency_us` (histogram)
  - `veyra_norm_engine_errors_total`
  - `veyra_control_contracts_loaded`

## Library contracts (fixtures)

Write the *content* of `linux_sshd.yaml` and `acme_ngfw_cef.yaml` (IF-CONTRACT-YAML) under `packages/veyra_engine/tests/contracts/`, with golden samples and expected outputs from the corpus. Until C2 ships the real compiler, use `veyra_engine.testing.mini_compile()`, a minimal compiler for tests only. C3 imports these files into the library.

## Tasks
- [ ] 1. decode + offset map; tests with ASCII, UTF-8 Hindi, Latin-1 and invalid bytes.
- [ ] 2. `spanjson` scanner with exhaustive tests (escapes, unicode escapes, nested, trailing text, malformed → error).
- [ ] 3. Peel layers syslog, json, kv, cef, leef, csv, regex, base64; `peel()` API.
- [ ] 4. template match + `template_sig` (test vectors).
- [ ] 5. map, time, enrich, validate, build, serialize, DLQ.
- [ ] 6. The linux_sshd and acme_ngfw_cef contracts + golden tests (≥ 20 samples each).
- [ ] 7. Normalizer service with transactions, the control follower and metrics.
- [ ] 8. **Determinism test:** normalize the corpus twice, in two processes → byte-identical `serialize()` output.
- [ ] 9. **Restart test:** kill -9 during a stream → no duplicates or gaps in `lineage` by `(event_uid, revision)`.
- [ ] 10. **Microbench:** `tools/bench/engine_bench.py` reports tier-1 EPS for single-core sshd and CEF. Record it in the report.

## Acceptance criteria
- [ ] AC1: Corpus sshd/CEF through Kafka → tier 1, schema-valid, `field_offsets` correct for every capture (checked by comparing raw bytes).
- [ ] AC2: The T1 authsrv line against a test contract (syslog → json → template) → tier 1, with `user.name` offset pointing at `r.patil` inside the JSON string.
- [ ] AC3: `garbage.bin` lines → tier 4, no exception, DLQ records with `reason_code`.
- [ ] AC4: The determinism and restart tests pass.
- [ ] AC5: Engine microbench ≥ 1500 EPS/core for tier 1 on the laptop (record the actual number).

## Settings
`VEYRA_NORM_BATCH_MAX` (500), `VEYRA_NORM_BATCH_MS` (100), `VEYRA_PEEL_MAX_DEPTH`, `VEYRA_ENGINE_BUDGET_US`, `VEYRA_MAX_EVENT_BYTES`.

## Risks

| Risk | Mitigation |
|---|---|
| jsonschema is slow | Cache validators; validate only the subset; **measure** |
| RE2 Python binding install issues | `google-re2` wheels exist for Linux and macOS; if blocked, use the `re` module with a strict pattern whitelist and the per-event budget (log a DEVIATION) |

## Implementation notes
_(filled after execution)_
