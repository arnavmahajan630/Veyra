"""LLM drafting bench (C4): accuracy, provenance, validity and latency per model.

python tools/bench/llm_bench.py build
python tools/bench/llm_bench.py run --models qwen2.5:3b,llama3.2:3b --machine laptop
python tools/bench/llm_bench.py warm | seed
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from veyra_common.framing import split_lines
from veyra_common.hashing import template_sig
from veyra_common.settings import settings
from veyra_contracts import compile
from veyra_contracts.drafting.cache import DraftCache
from veyra_contracts.drafting.drafter import Drafter
from veyra_contracts.drafting.generalize import generalize, new_contract
from veyra_contracts.drafting.ollama import OllamaClient
from veyra_contracts.drafting.request import Prepared, build
from veyra_contracts.drafting.verify import verify
from veyra_contracts.golden import GOLDEN_RECEIVED, golden_engine, golden_envelope

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "bench" / "llm_golden" / "cases.json"
EXTRA = REPO / "bench" / "llm_golden" / "extra.json"


def _expected(event: dict[str, Any], entries: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for entry in entries:
        if entry.kind == "capture":
            value: Any = event
            for part in entry.ocsf_path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            if value is not None:
                out[entry.ocsf_path] = str(value)
        elif entry.kind == "const" and entry.ocsf_path in ("status_id", "disposition_id"):
            out[entry.ocsf_path] = {"const": entry.value}
    return out


def _cases_from(contract_text: str, samples: list[bytes], prefix: str) -> list[dict[str, Any]]:
    compiled = compile(contract_text)
    engine = golden_engine(compiled)
    by_template: dict[str, dict[str, Any]] = {}
    for i, raw in enumerate(samples):
        result = engine.normalize(
            golden_envelope(raw, compiled, received_time=GOLDEN_RECEIVED, index=i)
        )
        template_id = (result.ulpf.get("template") or {}).get("id")
        if result.tier != 1 or template_id is None:
            continue
        template = next(t for t in compiled.templates if t.id == template_id)
        case = by_template.setdefault(
            template_id,
            {
                "id": f"{prefix}/{template_id}",
                "samples": [],
                "layers": compiled.envelope,
                "expected": _expected(result.ocsf, template.map),
            },
        )
        case["samples"].append(raw.decode("utf-8"))
    return list(by_template.values())


def build_cases(registry: Path, extra: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in sorted((registry / "library").glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        samples = [
            (registry / t["sample"]).read_bytes().rstrip(b"\n") for t in spec.get("tests", [])
        ]
        cases += _cases_from(path.read_text(encoding="utf-8"), samples, spec["contract"])
    for item in json.loads(extra.read_text(encoding="utf-8")):
        raws = [f.raw for f in split_lines((REPO / item["corpus"]).read_bytes())][: item["count"]]
        found = _cases_from((REPO / item["contract"]).read_text(encoding="utf-8"), raws, "extra")
        cases += [c | {"id": item["id"]} for c in found[:1]]
    return cases


def score(expected: dict[str, Any], got: dict[str, Any]) -> tuple[float, float]:
    exp = {(k, json.dumps(v, sort_keys=True)) for k, v in expected.items()}
    out = {(k, json.dumps(v, sort_keys=True)) for k, v in got.items()}
    if not exp and not out:
        return 1.0, 1.0
    hits = len(exp & out)
    return (hits / len(out) if out else 0.0, hits / len(exp) if exp else 0.0)


def _got(prepared: Prepared, response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for mapping in response.mappings:
        if mapping.const is not None:
            out[mapping.ocsf_path] = {"const": mapping.const}
        else:
            out[mapping.ocsf_path] = prepared.token(mapping.token).value
    return out


def run(models: list[str], machine: str) -> Path:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    rows = []
    for model in models:
        client = OllamaClient(
            settings.ollama_url,
            model,
            num_ctx=settings.llm_num_ctx,
            timeout_s=settings.llm_timeout_s,
            keep_alive=settings.llm_keep_alive,
        )
        drafter = Drafter(mode="live", cache=DraftCache(Path("/nonexistent")), client=client)
        precision, recall, latency = [], [], []
        valid = proven = 0
        for case in cases:
            raws = [s.encode("utf-8") for s in case["samples"]]
            prepared = build(raws, template_sig=case["id"], layers=case["layers"])
            started = time.perf_counter()
            outcome = drafter.draft(prepared)
            latency.append((time.perf_counter() - started) * 1000)
            valid += outcome.source.startswith("llm:")
            p, r = score(case["expected"], _got(prepared, outcome.response))
            precision.append(p)
            recall.append(r)
            template = generalize(prepared, outcome.response)
            contract = new_contract(
                contract_id="bench",
                tenant_id="t_bench",
                source_id="src_bench_01",
                layers=case["layers"],
                templates=[template],
                timezone="UTC",
                drafted_by="bench",
                draft_id="bench",
            )
            proven += verify(contract, raws, {template.id}).ok
        vram = sum(m.get("size_vram", 0) for m in client.ps()) / 2**30
        latency.sort()
        rows.append(
            (
                model,
                statistics.mean(precision),
                statistics.mean(recall),
                100 * proven / len(cases),
                100 * valid / len(cases),
                latency[len(latency) // 2],
                latency[int(len(latency) * 0.95) - 1],
                vram,
            )
        )
    report = REPO / "docs" / "plan" / "reports" / f"C4-bench-{machine}.md"
    lines = [
        f"# C4 LLM bench — {machine}",
        "",
        f"{len(cases)} cases from `bench/llm_golden/cases.json`.",
        "",
        "Verify % is the whole C4 verify (compile, provenance, types) on the drafted contract.",
        "JSON-valid % is the share of drafts the model produced; the rest fell back to the"
        " heuristic.",
        "",
        "| Model | Precision | Recall | Verify % | JSON-valid % | p50 ms | p95 ms | VRAM GiB |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {m} | {p:.2f} | {r:.2f} | {pv:.0f} | {jv:.0f} | {p50:.0f} | {p95:.0f} | {v:.1f} |"
        for m, p, r, pv, jv, p50, p95, v in rows
    ]
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def seed() -> None:
    client = OllamaClient(
        settings.ollama_url,
        settings.llm_model,
        num_ctx=settings.llm_num_ctx,
        timeout_s=settings.llm_timeout_s,
        keep_alive=settings.llm_keep_alive,
    )
    drafter = Drafter(
        mode="live_then_cache", cache=DraftCache(settings.llm_cache_dir), client=client
    )
    for name in ("authsrv_t1_ok.log", "authsrv_t2_session.log", "authsrv_t3_failed.log"):
        raws = [f.raw for f in split_lines((REPO / "demo" / "corpus" / name).read_bytes())][:5]
        probe = build(raws, template_sig="probe")
        sig = template_sig("authsrv", probe.text)
        outcome = drafter.draft(build(raws, template_sig=sig, layers=probe.layers))
        print(name, sig, outcome.source, f"{outcome.latency_ms:.0f} ms")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm_bench")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    run_p = sub.add_parser("run")
    run_p.add_argument("--models", required=True)
    run_p.add_argument("--machine", default=settings.profile)
    sub.add_parser("warm")
    sub.add_parser("seed")
    args = parser.parse_args(argv)
    if args.cmd == "build":
        cases = build_cases(settings.contracts_repo, EXTRA)
        CASES.parent.mkdir(parents=True, exist_ok=True)
        CASES.write_text(json.dumps(cases, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{len(cases)} cases → {CASES}")
    elif args.cmd == "run":
        print(run(args.models.split(","), args.machine))
    elif args.cmd == "warm":
        OllamaClient(
            settings.ollama_url,
            settings.llm_model,
            num_ctx=settings.llm_num_ctx,
            timeout_s=settings.llm_timeout_s,
            keep_alive=settings.llm_keep_alive,
        ).warm()
    else:
        seed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
