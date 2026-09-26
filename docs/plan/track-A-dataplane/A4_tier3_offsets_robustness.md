# A4 — Tier 3 generic extraction, classifier cascade, field offsets everywhere, robustness

```
track: A   owner: A   status: todo
contracts: v1.1
depends_on: [A3]                  unblocks: [CP2, C4 (tokens), B6 (highlights)]
consumes: [IF-ENGINE-LIB, IF-ULPF, IF-OCSF-SUBSET]
provides: [tier 3 per IF-ULPF, provenance_check(), extract_tokens() final, mask() final]
directories: [packages/veyra_engine/, services/normalizer/]
```

## Goal

Make "ultra-messy in, useful out" real. Unregistered or unknown-template events become **tier 3**:
- time, IPs, users, hosts and key/values are extracted with exact byte offsets;
- a low-confidence class hint is attached;
- everything else is preserved.

Also harden the engine so no input can crash or stall it.

This is what Wazuh shows for the messy source in Beat 3. It also produces the tokens the LLM drafter points at in Beat 4.

## Design

### Classifier cascade (no contract, or contract without a match)
Deterministic, in this order:
1. Syslog header?
2. Body starts with `{`/`[` → json.
3. `CEF:` → cef.
4. `LEEF:` → leef.
5. `k=v` token density ≥ 0.5 → kv.
6. Consistent delimiter count across the line (`;`, `|`, `,`, tab) ≥ 3 → csv (no header).
7. Otherwise, plain text.

Auto-peel with the detected layers, then kv-parse `json.__rest` if present. Record the cascade result in `ulpf.parse_path`, e.g. `["auto:syslog3164","auto:json","auto:kv(rest)"]`.

### `extract_tokens(text)` (final version; C4 and the UI depend on it)
- Returns ordered `Token(id="k<n>", value, start, end, kind, key)`, where `kind` is one of: `ip`, `ipv6`, `port`, `email`, `url`, `hostname`, `user`, `hash`, `uuid`, `timestamp`, `int`, `kv_value`, `word`, `quoted`.
- **User detection:** the value of keys matching `user|username|usr|account|acct|login|uid` (kv, JSON or `key:value`); tokens after `for user`, `user`, `by`, `account` in prose.
- **Hostname:** an FQDN-like token, or the syslog host.
- IDs are stable for identical text (determinism).

### Tier 3 build
- Observables from typed tokens, with names `ip_1`, `ip_2`, `user`, `host_1`, …
- IPs are **never** assigned to `src_endpoint` or `dst_endpoint` at tier 3.
- `unmapped` gets all kv, JSON fields and rest.
- `message` is the text field.
- `time` comes from the first timestamp token that parses (else received).
- `severity_id` from the severity-word vocabulary: `fail|failed|error|denied|blocked|critical|alert|panic`. Record it in `derived_fields` as `vocab:severity_words`.

### Class hint rules (`class_hint.py`)
A small table of keyword and field rules to `(class_uid, confidence)`:

| Rule | Hint |
|---|---|
| `login\|logon\|auth\|password\|ssh\|sudo` + user token | 3002 (medium if a user token is present, else low) |
| `allow\|deny\|drop\|accept` + 2 IPs + port | 4001 |
| `GET\|POST\|HTTP/1` | 4002 |
| `exec\|spawn\|pid` | 1007 |

The hint goes in `ulpf.class_hint`. Do not change `class_uid` (it stays 0).

### Field offsets everywhere: `provenance_check(event, raw_bytes) -> list[Check]`
- For every `field_offsets` entry, check that `raw_bytes[start:end]` decoded equals the value's raw representation (for JSON strings, the escaped form in raw).
- Every mapped path not in `field_offsets` must be in `derived_fields`.
- Returns per-path `ok` plus a reason.
- Exported, and used by tests, C4 drafts and B6 UI badges.

### `mask(text) -> (masked_text, mapping)`
Deterministic. Replaces:
- users → `<USER_n>`;
- emails → `<EMAIL_n>`;
- secrets (values of secret-like keys) → `<SECRET>`;
- long hex/base64 blobs → `<BLOB>`.

**IPs are kept**; they're needed for mapping and aren't secrets. Used for DLQ `text_masked` and LLM prompts.

### Robustness
- **RE2 only** inside `veyra_engine`. Add a test that fails if `import re` appears in engine modules outside an allowlist.
- **Budget:** check `perf_counter_ns` between stages. Over `ENGINE_BUDGET_US` → abort to tier 4 with `budget_exceeded`, keeping `raw_data`.
- **Size:** over `MAX_EVENT_BYTES`, parse only the first N bytes, set a flag, and keep the full raw in `raw_data`.
- **Exceptions:** `normalize()` has a top-level guard. Any exception → tier 4 `engine_crash` with the exception class name in `reason_detail`. Never raise.
- **Crash-loop guard** (normalizer): persist the in-flight `(topic, partition, offset)` to `data/state/normalizer_inflight` before processing a batch. On startup, if the same offset appears `VEYRA_POISON_MAX_RETRIES` times, emit tier 4 `engine_crash` for that record and skip it.

## Tasks
- [ ] 1. Classifier cascade + auto-peel; tests with the OT historian lines, T3 lines and garbage.
- [ ] 2. Final `extract_tokens`; property tests (spans always slice to the value).
- [ ] 3. Tier 3 build, class hints, severity vocabulary.
- [ ] 4. `provenance_check` + `mask`; export both from `veyra_engine`.
- [ ] 5. Robustness guards + the RE2 import test.
- [ ] 6. **Fuzz:** a `hypothesis` strategy of random bytes, corpus mutations and deep JSON nesting. `normalize` never raises, always returns tier 1–4, within 2× budget.
- [ ] 7. Update the golden tests; add tier 3 goldens for T3 and the OT historian.

## Acceptance criteria
- [ ] AC1: T3 (multi-line, syslog + JSON + kv + trace) → tier 3.
  - Observables contain `103.21.4.77`, `10.2.3.4` and `a.sharma`.
  - `unmapped` has `attempts=1` and `trace=…`.
  - `class_hint` = 3002 medium.
  - 100% of offsets pass `provenance_check`.
- [ ] AC2: The OT historian line with a Hindi field → tier 3; offsets are correct for the Devanagari text (byte slices verified).
- [ ] AC3: The fuzz run (≥ 50k cases) has zero exceptions.
- [ ] AC4: Pathological input (a 60 KB line of `a=` repeated) completes within 2× budget as tier 4 `budget_exceeded`, or as tier 3.
- [ ] AC5: `mask()` on T3 → `user=<USER_1> FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1`.

## Settings
`VEYRA_POISON_MAX_RETRIES` (3).

## Implementation notes
_(filled after execution)_
