#!/usr/bin/env bash
set -eo pipefail

# Veyra Automated Benchmark Suite
# Generates comprehensive performance metrics per the Handoff Checklist.
# Assumes the Veyra stack is already running.
# IMPORTANT: For best results and no background noise, bring up the stack using:
#   ./veyra.sh clean

BENCH_DIR="bench/results"
mkdir -p "$BENCH_DIR"
TIMESTAMP=$(date +"%Y-%m-%dT%H:%M:%SZ")

echo "============================================================"
echo " Starting Veyra Benchmark Suite"
echo "============================================================"

# 1. Capture Environment Metadata
echo "[1/5] Capturing Hardware & Environment Metadata..."
cat <<EOF > "$BENCH_DIR/environment.json"
{
  "timestamp": "$TIMESTAMP",
  "os": "$(uname -srm)",
  "cpu": "$(lscpu | grep 'Model name' | sed -E 's/^Model name:\s*//' || echo 'Unknown')",
  "cores": "$(nproc)",
  "memory": "$(free -h | awk '/^Mem:/ {print $2}')",
  "docker_version": "$(docker --version || echo 'N/A')",
  "git_sha": "$(git rev-parse HEAD 2>/dev/null || echo 'N/A')"
}
EOF
echo "  -> Saved to $BENCH_DIR/environment.json"

# 2. Engine Microbenchmarks
echo "[2/5] Running Engine Microbenchmarks (Single-core parsing & validation)..."
uv run python tools/bench/engine_bench.py --json > "$BENCH_DIR/engine_microbench.json"
echo "  -> Saved to $BENCH_DIR/engine_microbench.json"

# 3. LLM Benchmarks (Conditional)
echo "[3/5] Checking LLM Model status..."
if uv run python tools/bench/llm_bench.py warm > /dev/null 2>&1; then
    echo "  -> LLM backend is responsive. Running LLM benchmarks..."
    uv run python tools/bench/llm_bench.py run > "$BENCH_DIR/llm_bench.txt" 2>&1 || true
    echo "  -> Saved to $BENCH_DIR/llm_bench.txt"
else
    echo "  -> LLM backend not responsive or not configured. Skipping LLM benchmarks."
fi

# 4. End-to-End Pipeline & Scalability
echo "[4/5] Running Scalability & Throughput Benchmarks (1, 2, 4 workers)..."
echo "timestamp,container,cpu_percent,mem_usage" > "$BENCH_DIR/resources.csv"
(
  while true; do
    docker stats --no-stream --format "{{.Name}},{{.CPUPerc}},{{.MemUsage}}" | sed "s/^/$(date +%s),/" >> "$BENCH_DIR/resources.csv" || true
    sleep 5
  done
) &
STATS_PID=$!
trap 'kill $STATS_PID 2>/dev/null || true' EXIT

uv run python tools/bench/throughput.py --count 50000 --replicas 1,2,4 --report || true

# Explicitly kill if successful
kill $STATS_PID 2>/dev/null || true
trap - EXIT
wait $STATS_PID 2>/dev/null || true

# Find the generated report in docs/plan/reports/ and copy it
LATEST_REPORT=$(ls -t docs/plan/reports/A6-bench-*.md 2>/dev/null | head -n 1)
if [ -n "$LATEST_REPORT" ]; then
    cp "$LATEST_REPORT" "$BENCH_DIR/throughput_report.md"
    echo "  -> Throughput report saved to $BENCH_DIR/throughput_report.md"
fi
echo "  -> Resource stats saved to $BENCH_DIR/resources.csv"

# 5. ClickHouse Query Latency
echo "[5/5] Running ClickHouse Query Benchmarks..."
echo "  -> Seeding 1,000,000 synthetic rows..."
uv run python tools/seed_ch.py --db veyra_bench --rows 1000000 --reset > /dev/null 2>&1
echo "  -> Benchmarking queries..."
uv run python tools/bench/ch_queries.py --db veyra_bench --json "$BENCH_DIR/ch_queries.json" > /dev/null || true
echo "  -> Saved to $BENCH_DIR/ch_queries.json"

# 6. Report Compilation
echo "[6/6] Compiling Final Benchmark Report..."
uv run python tools/bench/compile_report.py "$BENCH_DIR" > "$BENCH_DIR/BENCHMARK_REPORT.md"

echo "============================================================"
echo " Benchmark Complete!"
echo " Results saved in: $BENCH_DIR/"
echo " Final Report: $BENCH_DIR/BENCHMARK_REPORT.md"
echo "============================================================"
