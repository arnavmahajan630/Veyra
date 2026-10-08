import json
import sys
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print("Usage: compile_report.py <bench_dir>")
        sys.exit(1)

    bench_dir = Path(sys.argv[1])
    report = ["# Veyra Benchmark Report\n"]
    
    # 1. Environment
    env_file = bench_dir / "environment.json"
    if env_file.exists():
        env = json.loads(env_file.read_text())
        report.append("## Hardware / Environment Metadata")
        report.append(f"- **Timestamp**: {env.get('timestamp', 'N/A')}")
        report.append(f"- **OS**: {env.get('os', 'N/A')}")
        report.append(f"- **CPU**: {env.get('cpu', 'N/A')} ({env.get('cores', 'N/A')} cores)")
        report.append(f"- **Memory**: {env.get('memory', 'N/A')}")
        report.append(f"- **Docker**: {env.get('docker_version', 'N/A')}")
        report.append(f"- **Git SHA**: {env.get('git_sha', 'N/A')}\n")

    # 2. Engine Microbenchmarks
    engine_file = bench_dir / "engine_microbench.json"
    if engine_file.exists():
        try:
            results = json.loads(engine_file.read_text())
            report.append("## Engine Microbenchmark (Single-core Parsing)")
            report.append("| Workload | EPS | p50 latency (µs) | p95 latency (µs) | Validation (µs) | Tiers |")
            report.append("|----------|-----|------------------|------------------|-----------------|-------|")
            for name, data in results.items():
                report.append(
                    f"| {name} | {data.get('eps', 0):.1f} | {data.get('p50_us', 0):.1f} | "
                    f"{data.get('p95_us', 0):.1f} | {data.get('validate_us', 0):.1f} | "
                    f"{data.get('tiers', '')} |"
                )
            report.append("\n")
        except Exception as e:
            report.append(f"*(Engine benchmark data unavailable: {e})*\n")
            
    # 3. ClickHouse Query Benchmarks
    ch_file = bench_dir / "ch_queries.json"
    if ch_file.exists():
        try:
            ch_data = json.loads(ch_file.read_text())
            report.append("## ClickHouse Lineage Queries (1M events)")
            report.append("| Query | p50 (ms) | p95 (ms) | Target p95 |")
            report.append("|-------|----------|----------|------------|")
            target = ch_data.get("target_p95_ms", 50)
            for name, data in ch_data.get("results", {}).items():
                p50 = data.get("p50_ms", 0)
                p95 = data.get("p95_ms", 0)
                pass_fail = "✅" if p95 <= target else "❌"
                report.append(f"| {name} | {p50:.1f} | {p95:.1f} | {target} {pass_fail} |")
            report.append("\n")
        except Exception as e:
            report.append(f"*(ClickHouse benchmark data unavailable: {e})*\n")

    # 4. End-to-End Throughput (Appends the pre-generated Markdown)
    throughput_file = bench_dir / "throughput_report.md"
    if throughput_file.exists():
        report.append("## End-to-End Pipeline Throughput & Scalability")
        report.append(throughput_file.read_text())
    else:
        report.append("*(Throughput benchmark data unavailable)*\n")

    # Resource logs note
    res_file = bench_dir / "resources.csv"
    if res_file.exists():
        report.append("\n## Resource Utilization")
        report.append(f"Raw system metrics (CPU/Memory) during throughput test are available in `{res_file.name}`.\n")

    print("\n".join(report))

if __name__ == "__main__":
    main()
