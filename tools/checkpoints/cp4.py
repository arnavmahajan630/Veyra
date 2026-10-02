"""CP4 — Demo freeze (docs/plan/shared/S1_integration_checkpoints.md).

    make cp4                 # one reset + one run, plus the headroom and fallback checks
    make cp4 RUNS=10         # criterion 3: ten consecutive reset + auto runs

Five criteria, all about repeatability: the reset is inside its budget, the scripted run holds
every assertion, ten runs in a row do, there is memory headroom at the peak, and the LLM
fallback works with Ollama stopped.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# `tools/` on the path, so `checkpoints._common` and `veyra_lib` import whether this is run as
# a script, as a module, or through the compose `tools` service.
_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from checkpoints._common import FAIL, PASS, WARN, Row, run, sh, verdict  # noqa: E402

from veyra_common.settings import Settings  # noqa: E402

CFG = Settings()
RUNS = int(os.environ.get("RUNS", "1"))
HEADROOM_GB = 3.0


def _cli(*args: str, timeout: int = 1200) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "demo_engine.cli", "--in-container", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _free_gb() -> float:
    """Host memory the stack is not using, as Docker sees it."""
    _, out = sh("docker", "info", "--format", "{{.MemTotal}}")
    try:
        total_gb = int(out.strip()) / 1024**3
    except ValueError:
        return -1.0
    _, stats = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")
    used_gb = 0.0
    for line in stats.splitlines():
        if "\t" not in line or not line.startswith("veyra-"):
            continue
        value = line.split("\t", 1)[1].split("/")[0].strip()
        number = "".join(c for c in value if c.isdigit() or c == ".")
        if not number:
            continue
        scale = {"KiB": 1 / 1024**2, "MiB": 1 / 1024, "GiB": 1.0, "B": 1 / 1024**3}
        for suffix, factor in scale.items():
            if value.endswith(suffix):
                used_gb += float(number) * factor
                break
    return total_gb - used_gb


# ---------------------------------------------------------------- 1
def c1_reset_in_budget() -> list[Row]:
    started = time.monotonic()
    code, out = _cli("reset")
    seconds = time.monotonic() - started
    budget = float(CFG.demo_reset_budget_s)
    tail = out.splitlines()[-1] if out else ""
    return [
        verdict(code == 0, "reset reported every step ok", tail),
        verdict(
            seconds < budget,
            f"reset under {budget:.0f}s",
            f"{seconds:.0f}s",
        ),
    ]


# ---------------------------------------------------------------- 2
def c2_scripted_run() -> list[Row]:
    code, out = _cli("auto", "1")
    tail = [line for line in out.splitlines() if line.strip()][-3:]
    return [verdict(code == 0, "every beat assertion held", " | ".join(tail)[:200])]


# ---------------------------------------------------------------- 3
def c3_ten_runs() -> list[Row]:
    if RUNS < 2:
        return [
            Row(
                WARN,
                "ten consecutive reset + auto runs",
                "pass RUNS=10 to run this; a single run is criterion 2",
            )
        ]
    rows: list[Row] = []
    timings: list[float] = []
    for attempt in range(1, RUNS + 1):
        started = time.monotonic()
        reset_code, _ = _cli("reset")
        auto_code, out = _cli("auto", "1")
        timings.append(time.monotonic() - started)
        ok = reset_code == 0 and auto_code == 0
        rows.append(
            Row(
                PASS if ok else FAIL,
                f"run {attempt} of {RUNS}",
                f"{timings[-1]:.0f}s" + ("" if ok else f": {out.splitlines()[-1][:120]}"),
            )
        )
    rows.append(
        verdict(
            all(row.status == PASS for row in rows),
            f"{RUNS}/{RUNS} green",
            f"median {sorted(timings)[len(timings) // 2]:.0f}s, worst {max(timings):.0f}s",
        )
    )
    return rows


# ---------------------------------------------------------------- 4
def c4_memory_headroom() -> list[Row]:
    """Sampled while a run is in flight, because idle headroom is not the question."""
    samples: list[float] = []
    stop = threading.Event()

    def sample() -> None:
        while not stop.is_set():
            samples.append(_free_gb())
            time.sleep(3)

    watcher = threading.Thread(target=sample, daemon=True)
    watcher.start()
    try:
        _cli("auto", "1")
    finally:
        stop.set()
        watcher.join(timeout=10)
    usable = [s for s in samples if s >= 0]
    if not usable:
        return [Row(FAIL, "memory headroom", "could not read docker info / docker stats")]
    worst = min(usable)
    return [
        verdict(
            worst >= HEADROOM_GB,
            f"at least {HEADROOM_GB:g} GB free at the peak",
            f"worst {worst:.1f} GB over {len(usable)} sample(s)",
        )
    ]


# ---------------------------------------------------------------- 5
def c5_cache_fallback_without_ollama() -> list[Row]:
    """With Ollama stopped, `live_then_cache` must fall back and the demo still pass."""
    cache = CFG.llm_cache_dir
    cached = sorted(cache.glob("*")) if cache.is_dir() else []
    detail = (
        f"{len(cached)} file(s) in {cache}"
        if cached
        else f"{cache} is empty: run `make llm-cache-seed` first"
    )
    rows = [verdict(bool(cached), "the LLM cache holds a recorded draft", detail)]
    if not cached:
        return rows

    stopped, _ = sh("docker", "stop", "veyra-ollama", timeout=120)
    try:
        env = {**os.environ, "VEYRA_LLM_MODE": "live_then_cache"}
        proc = subprocess.run(
            [sys.executable, "-m", "demo_engine.cli", "--in-container", "auto", "1"],
            capture_output=True,
            text=True,
            timeout=1200,
            env=env,
        )
        out = (proc.stdout + proc.stderr).strip().splitlines()
        rows.append(
            verdict(
                proc.returncode == 0,
                "the demo passes with Ollama stopped",
                out[-1][:160] if out else "",
            )
        )
    finally:
        if stopped == 0:
            sh("docker", "start", "veyra-ollama", timeout=120)
    return rows


CHECKS = {
    "1": ("the reset is inside its budget", c1_reset_in_budget),
    "2": ("the scripted run holds every assertion", c2_scripted_run),
    "3": ("ten consecutive reset + auto runs", c3_ten_runs),
    "4": ("memory headroom during a run", c4_memory_headroom),
    "5": ("the cached draft carries the demo without Ollama", c5_cache_fallback_without_ollama),
}

if __name__ == "__main__":
    raise SystemExit(run("CP4", CHECKS))
