# C4 LLM bench — laptop

27 cases from `bench/llm_golden/cases.json`.

Verify % is the whole C4 verify (compile, provenance, types) on the drafted contract.
JSON-valid % is the share of drafts the model produced; the rest fell back to the heuristic.

| Model | Precision | Recall | Verify % | JSON-valid % | p50 ms | p95 ms | VRAM GiB |
|---|---|---|---|---|---|---|---|
| qwen2.5:3b | 0.36 | 0.34 | 63 | 85 | 2145 | 5288 | 2.0 |
| llama3.2:3b | 0.59 | 0.47 | 74 | 56 | 3262 | 6268 | 2.4 |

<!-- Hand-written below. `make bench-llm` rewrites this whole file, so carry these notes over. -->

**Machine:** RTX 4050 Laptop GPU (6 GiB), driver 596.21, Ollama 0.34.4 on the Windows host, both models
Q4 at `num_ctx` 4096, 100% on GPU, resident together at 4.75 GiB. `LLM_TIMEOUT_S` 25. Run 2026-09-29.
Wall time 179 s for both models. Cold load: 26–32 s on first boot, 14 s from the OS file cache.

The analysis (per-case failures, the T3 result, and why `llm-cache-seed` was not run) is in
[C4.md](C4.md#live-results-on-the-rtx-4050-2026-09-29).
