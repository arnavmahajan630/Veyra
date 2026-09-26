# C4 — LLM drafter: token-reference drafting, provenance, heuristic fallback, cache, onboarding analyze, bench

```
track: C   owner: C   status: todo
contracts: v1.1
depends_on: [C2, C3, A4]     unblocks: [CP3, C6, Beats 2 and 4]
consumes: [IF-LLM-DRAFT, IF-ENGINE-LIB (extract_tokens, mask, peel, provenance_check, backtest), IF-OCSF-SUBSET]
provides: [drafts, POST /onboarding/analyze, GET/PATCH /drafts/*, make bench-llm, make llm-warm]
directories: [packages/veyra_contracts/drafting/, services/control_api/ (drafts, onboarding), tools/bench/llm_bench.py, bench/llm_golden/]
```

## Goal

Draft a Log Contract template from a handful of samples using a **small local LLM** that can only *point at tokens that exist in the real log* and choose from a closed vocabulary. Then verify it mechanically (provenance + backtest), so a wrong draft is visible before any human approves it. Fall back to deterministic heuristics or recorded drafts, so the demo never waits on the model.

v1 refs: §11.2 (Pack Studio: local LLM drafts only), ADR-04. This is the "AI, but safe" story of Beats 2 and 4.

## Design

### Request building (`drafting/request.py`)
1. Pick the template text from each sample: peel with the source's contract envelope if one exists, else with the A4 classifier cascade; take the text field.
2. `tokens = extract_tokens(text_of_sample_0)`, then align them with the other samples by position in the `drain_template`. Tokens whose value varies across samples are **variables**; the rest are literals.
3. The masked samples come from `mask()`. Users become `<USER_n>` in the *prompt* only; token ids still refer to real spans.
4. `allowed_classes`, `allowed_fields` (the IF-OCSF-SUBSET catalogue, filtered to the plausible classes from A4's `class_hint` plus a general set) and `enums`.

### Prompt
- A short system prompt: role, rules ("only use token ids from the list; only use const values from enums; if unsure, omit the mapping"), and the output schema.
- 2 compact few-shot examples: sshd and CEF, taken from the library packs.
- Target prompt size < 2.5k tokens (fits `LLM_NUM_CTX` 4096 on the laptop).

### Ollama call (`drafting/ollama.py`)
- `POST {OLLAMA_URL}/api/chat` with `format = <JSON schema of the IF-LLM-DRAFT response>`, `options: {temperature: 0, seed: 7, num_ctx}`, `keep_alive: "30m"` and `stream: false`.
- Timeout `LLM_TIMEOUT_S`.
- Validate the response with Pydantic. Invalid → one retry with the validation error appended → otherwise fall back.

### Post-processing (`drafting/generalize.py`)
- Map each chosen token id to a capture named after the OCSF path's last segment (with suffixes for uniqueness), and typed by token kind: ip → `:ip`, int → `:int`, else a plain `<name>`.
- Variable tokens not chosen become `<*>`. Literal tokens are kept.
- Key=value tokens keep their key literal: `user=<user>`.
- Build the template YAML fragment (IF-CONTRACT-YAML): `pattern`, `class`, `activity`, `map` (captures + consts), `unmapped` (the remaining variable kv keys).
- Merge it into the source's contract as a new version (or a new contract for onboarding).

### Verification (`drafting/verify.py`)
1. Compile (C2).
2. `provenance_check` on every sample's normalized output: each mapping row gets ✓ or ✗ with a reason.
3. Type checks: an ip field has an ip token; `time` parses.
4. Backtest (C2) against the DLQ events for the sig (drift) or against the pasted samples (onboarding).

The draft stores all of this. The UI shows it.

### Heuristic drafter (`drafting/heuristic.py`)
Deterministic rules over tokens:
- IP after `from|src|source|client` → `src_endpoint.ip`;
- IP after `to|dst|via|dest|server` → `dst_endpoint.ip`;
- port after `port|dpt|spt` → the matching `.port`;
- user tokens → `user.name`;
- `FAILED|failure|denied|invalid` → `status_id` 2;
- `OK|success|accepted` → 1;
- the class from A4's `class_hint`.

Same output shape as the LLM. It is used when `LLM_MODE=heuristic`, or when live and cache both fail.

### Cache (`data/llm_cache/<template_sig>.json`)
Stores the raw LLM response + model + prompt hash.

| Mode | Behaviour |
|---|---|
| `live` | Call the model; on failure, use the heuristic |
| `cache` | Use the cache; on a miss, use the heuristic |
| `live_then_cache` | Try live within `LLM_TIMEOUT_S`; on timeout or failure use the cache; then the heuristic. Successful live results are **written** to the cache. |

The UI badge shows the source: `llm:<model>`, `cache:<model>`, or `heuristic`. `make llm-cache-seed` pre-records drafts for the demo sigs (T1, T2, T3) on the demo machine.

### Onboarding analyze (`POST /onboarding/analyze`)
1. Stamp the samples as envelopes (in-memory; not sent to Kafka).
2. Run the classifier cascade + peel preview (layers found, text field).
3. Group by `template_sig` + Drain3 (an ephemeral miner).
4. `library_match` (C3). On a match, propose the library pack and stop.
5. Otherwise, one draft per template group (in parallel, bounded to 1 concurrent LLM call on the laptop).
6. Assemble into one contract draft for the new source.

The response is streamed as SSE `draft` events, so the UI fills in progressively.

### Bench (`make bench-llm`, `tools/bench/llm_bench.py`)
`bench/llm_golden/` holds 20 templates, each with samples and the expected mapping set. They cover:
- sshd;
- CEF;
- nginx;
- 5 custom app formats (including the authsrv T1/T2/T3);
- a Windows-like kv format;
- the OT historian (with the Hindi field);
- JSON-in-syslog;
- a key:value prose format;
- the rest mixed.

For each model in `--models a,b,c`, report:
- field-level precision and recall vs expected;
- the provenance pass rate (should be 100% by construction; any failure is a bug);
- the JSON validity rate;
- p50/p95 latency;
- VRAM (from `ollama ps`).

Output: `reports/C4-bench-<machine>.md`. The profile's `LLM_MODEL` is set from the winner (`03_INFRA_PROFILES.md` §4).

`make llm-warm`: load the model (`keep_alive` 30m) and run one tiny request.

## Tasks
- [ ] 1. Request builder + alignment + masking; unit tests on T3 samples.
- [ ] 2. The Ollama client with schema-constrained output, retry and timeout; a mocked-server test.
- [ ] 3. Generalization → YAML; round-trip through the C2 compiler.
- [ ] 4. Verify pipeline (compile, provenance, types, backtest) + draft storage + `GET/PATCH /drafts/{id}` (PATCH re-runs verify) + `POST /drafts/{id}/submit`.
- [ ] 5. The heuristic drafter + cache + modes + badges.
- [ ] 6. Onboarding analyze with SSE progress.
- [ ] 7. Golden bench set + bench runner + report; `llm-warm`; `llm-cache-seed`.

## Acceptance criteria
- [ ] AC1: The T3 drift item → the draft maps `user.name`, `src_endpoint.ip`, `dst_endpoint.ip` and `status_id=2`, class Authentication/Logon; provenance is 100% ✓; backtest 8/8 → tier 1. Holds in all three modes (live, cache, heuristic).
- [ ] AC2: Live draft latency on the laptop p95 ≤ `LLM_TIMEOUT_S`; `live_then_cache` never exceeds `LLM_TIMEOUT_S` + 200 ms.
- [ ] AC3: A deliberately wrong edit in PATCH (map `src_endpoint.ip` to the destination token) → provenance still ✓ (bytes exist) but the backtest shows a changed field, and the type check flags nothing. This demonstrates why human review remains. Document it in the report as the known limit and the Q&A answer.
- [ ] AC4: Onboarding analyze with T1+T2 samples → 2 templates, no library match, a draft ready in < 8 s (live) or < 1 s (cache).
- [ ] AC5: The bench report exists for the laptop, with at least 2 models compared.

## Settings
`VEYRA_LLM_MODEL`, `VEYRA_LLM_MODE`, `VEYRA_LLM_TIMEOUT_S`, `VEYRA_LLM_NUM_CTX`, `VEYRA_OLLAMA_URL`, `VEYRA_LLM_MAX_CONCURRENCY` (1 on laptop).

## Risks

| Risk | Mitigation |
|---|---|
| A small model picks the wrong tokens | Heuristic cross-check: if the LLM and heuristic disagree on a path, flag the row "review" (amber) in the UI |
| VRAM contention with display | Keep the model at Q4; close other GPU apps; measure in the bench |

## Implementation notes
_(filled after execution)_
