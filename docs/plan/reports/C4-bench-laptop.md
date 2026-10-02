# C4 LLM bench — laptop

27 cases from `bench/llm_golden/cases.json`.

Verify % is the whole C4 verify (compile, provenance, types) on the drafted contract.
Valid % is the share of drafts the model produced; the rest fell back to the heuristic.
A `decision:` model answers multiple-choice questions instead of writing JSON.

| Model | Precision | Recall | Verify % | Valid % | p50 ms | p95 ms | VRAM GiB |
|---|---|---|---|---|---|---|---|
| qwen2.5:3b | 0.46 | 0.45 | 81 | 100 | 1491 | 2990 | 2.0 |
| llama3.2:3b | 0.48 | 0.59 | 81 | 93 | 1542 | 3781 | 2.4 |
| decision:laya:en | 0.44 | 0.33 | 81 | 100 | 87 | 311 | 0.8 |

<!-- Hand-written below. `make bench-llm` keeps everything from this line down. -->

**Machine:** RTX 4050 Laptop GPU (6 GiB). Ollama 0.35.0 on the Windows host, both models Q4 at
`num_ctx` 4096. Ollaya 0.9.0 in Docker (`ghcr.io/ollaya-dev/ollaya:cuda`) serving `laya:en` (421M,
ONNX, fp16) on the same GPU. `LLM_TIMEOUT_S` 25. Run 2026-10-02, all three models warm.

**Against the 2026-09-29 run** (same 27 cases, before the answers were limited to fixed lists):

| Model | Precision | Recall | Verify % | Valid % | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| qwen2.5:3b, before | 0.36 | 0.34 | 63 | 85 | 2145 | 5288 |
| qwen2.5:3b, now | 0.46 | 0.45 | 81 | 100 | 1491 | 2990 |
| llama3.2:3b, before | 0.59 | 0.47 | 74 | 56 | 3262 | 6268 |
| llama3.2:3b, now | 0.48 | 0.59 | 81 | 93 | 1542 | 3781 |

llama's precision fell because it now drafts 93% of the cases itself instead of handing 44% of
them to the heuristic, and its own drafts add fields the golden contracts leave out.

The analysis (per-case results, T3, the decision model) is in
[C4.md](C4.md#fixed-answer-lists-and-a-decision-model-backend-2026-10-02). The earlier run is in
[C4.md](C4.md#live-results-on-the-rtx-4050-2026-09-29).
