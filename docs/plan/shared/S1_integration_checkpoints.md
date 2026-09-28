# S1 — Integration checkpoints (CP1–CP4)

```
track: shared   owner: A+B+C   status: todo
contracts: v1.4
```

Each checkpoint is a script plus a human walkthrough. The first run of each checkpoint is done by all three together. Record every run in `reports/CP<n>-<date>.md` and in the status board's checkpoint log.

Every checkpoint script lives in `tools/checkpoints/cp<n>.py`, is run by `make cp<n>`, and prints PASS/FAIL per criterion. Scripts only *observe*: they send inputs through the public paths (syslog, HTTP, APIs) and check outputs in Kafka, ClickHouse, Wazuh's sink file and the APIs.

---

## CP1 — First light

**Needs:** S0; A1; A3 (tier 1 and tier 4); A6 (minimal Wazuh route); B1; B2; C1 (publishes seeded contracts to `control`).

| # | Check | Pass |
|---|---|---|
| 1 | Send 10 `linux_sshd` lines via UDP to 5524 from a container at the registered IP | 10 envelopes on `raw.linux`, source resolved, `raw_sha256` correct (the checker recomputes it) |
| 2 | The same 10 reach `norm.iam` | Tier 1, `class_uid` 3002, `ulpf.contract.id=linux_sshd` |
| 3 | The same 10 appear in `data/sinks/wazuh/veyra.ndjson` and in Wazuh Discover (human looks) | 10 lines; rule 100100 visible |
| 4 | Garbage lines from an unknown IP | On `raw.unregistered`; tier 4 in Wazuh; nothing crashed (`/healthz` all green) |
| 5 | A segment seals within `SEGMENT_MAX_SECONDS` | Segment file exists, mode 0444; `vault_index` sealed record present |
| 6 | ClickHouse | `raw_events` and `norm_lineage` counts match the sent count |
| 7 | Kill the normalizer mid-stream, restart it | No duplicates in `norm_lineage` by `(event_uid, revision)`; no gaps |

## CP2 — Messy + evidence

**Needs:** CP1; A4; B3; B4; C2; C5.

| # | Check | Pass |
|---|---|---|
| 1 | Send `authsrv_t3_failed.log` via syslog TCP with a multi-line trace, from an IP mapped to `src_authsrv_01`. The contract covers only T1 and T2 at this point. | One envelope per logical event (framing `multiline_join`); tier 3; observables contain both IPs and the user; `class_hint` 3002 |
| 2 | `ulpf.field_offsets` for every extracted value | `raw[start:end]` equals the value, for 100% of fields (the checker verifies) |
| 3 | `GET /api/evidence/verify/{uid}` on a sealed event | `ok=true`, 8 steps, total < 2 s |
| 4 | A signed root exists per window; the immudb verified-get succeeds; the ledger chain links (`prev_signed_sha256`) | yes |
| 5 | Console Overview shows live tiers and EPS; Sources shows tier mix (human looks) | Updates within 2 s |
| 6 | The contract compiler compiles the seeded contracts; golden tests pass | yes |

## CP3 — Loop closed

**Needs:** CP2; A2; A5; B5; B6; C3; C4; C6.

| # | Check | Pass |
|---|---|---|
| 1 | Onboarding via API: analyze T1 and T2 samples → draft → submit → approve (different user) → key issued | Contract `authsrv@1` active; key works on the HEC endpoint |
| 2 | Push T3 ×8 via HEC with the key | Tier 3; drift item appears within 10 s |
| 3 | Draft on the drift item (`LLM_MODE=live`, then again with `cache`) | Provenance all ok; backtest 8/8 tier 3→1, 0 regressions |
| 4 | Approve with the **same user** | HTTP 403 (four-eyes) |
| 5 | Approve with the approver → promote → replay | 8 events at revision 2, tier 1, in Wazuh; brute-force rule 100111 fires for `103.21.4.77` (human looks) |
| 6 | Lineage page: field hover highlights bytes; revision timeline shows 1→2 (human looks) | yes |
| 7 | Tamper `insider_rewrite` on a replayed event → verify | `ok=false`; failing steps identified; untamper → `ok=true` again |

## CP4 — Demo freeze

**Needs:** CP3; B7; S2 items in progress.

| # | Check | Pass |
|---|---|---|
| 1 | `make demo-reset` | < 90 s; pre-demo state exactly per `04_DEMO_SCRIPT.md` §2 |
| 2 | Scripted demo run (`make demo-auto`, which triggers stages with the script's timings) | All beat assertions pass |
| 3 | 10 consecutive runs of reset + auto | 10/10 pass; record timings |
| 4 | Memory headroom during the run | ≥ 3 GB free at the peak |
| 5 | `LLM_MODE=live` with Ollama stopped | Cache fallback engages; the demo still passes |

After CP4: tag `demo-freeze-1`. From then on, only bug fixes from S2 are merged.

## Common failure modes (keep this list growing)

| Symptom | Likely cause |
|---|---|
| Events on `raw.*` but nothing on `norm.*` | Normalizer's `control` state is empty → C1 publish didn't run, or the normalizer didn't read the compacted topic from the beginning |
| Duplicates after a restart | Offsets committed outside the transaction |
| Wazuh shows nothing | `localfile` path mismatch, JSON not one-per-line, or rule 100100 missing (level too low to alert) |
| Field offsets wrong | Offsets computed on decoded text vs raw bytes (must be bytes of the decoded UTF-8; see IF-ULPF) |
