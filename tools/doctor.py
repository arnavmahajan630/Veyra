"""`make doctor` — does this host have what the profile needs?

Run it on a new machine before `make up` (03_INFRA_PROFILES.md §10 hardware switching).
It only reports; it never changes anything. PASS/WARN/FAIL per check, exit 1 on any FAIL.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
Result = tuple[str, str, str]  # (status, check, detail)


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def check_tools() -> list[Result]:
    out: list[Result] = []
    for tool, needed in (("docker", True), ("uv", True), ("node", False), ("ollama", False)):
        path = shutil.which(tool)
        version = _run(tool, "--version").splitlines()[0] if path else ""
        if path:
            out.append((PASS, tool, version))
        else:
            out.append((FAIL if needed else WARN, tool, "not installed"))
    compose = _run("docker", "compose", "version")
    out.append((PASS if compose else FAIL, "docker compose", compose or "not available"))
    return out


def check_memory() -> list[Result]:
    try:
        lines = Path("/proc/meminfo").read_text().splitlines()
        meminfo = {
            parts[0].rstrip(":"): int(parts[1])
            for parts in (line.split() for line in lines)
            if len(parts) >= 2 and parts[1].isdigit()
        }
    except OSError:
        return [(WARN, "memory", "cannot read /proc/meminfo (not Linux?)")]
    total_gb = meminfo.get("MemTotal", 0) / 1024 / 1024
    avail_gb = meminfo.get("MemAvailable", 0) / 1024 / 1024
    swap_gb = meminfo.get("SwapTotal", 0) / 1024 / 1024
    status = PASS if total_gb >= 15 else WARN
    results = [(status, "RAM total", f"{total_gb:.1f} GB (laptop profile assumes 16 GB)")]
    # The laptop budget is ~11 GB with Wazuh; the demo must never actually swap.
    results.append(
        (PASS if avail_gb >= 9 else WARN, "RAM available", f"{avail_gb:.1f} GB (need ~9 GB free)")
    )
    results.append(
        (PASS if swap_gb >= 8 else WARN, "swap", f"{swap_gb:.1f} GB (03 §3 wants >= 8 GB)")
    )
    return results


def check_disk() -> list[Result]:
    usage = shutil.disk_usage(REPO)
    free_gb = usage.free / 1024**3
    return [(PASS if free_gb >= 10 else FAIL, "disk free", f"{free_gb:.0f} GB (need >= 10 GB)")]


def check_gpu() -> list[Result]:
    smi = _run("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    if not smi:
        return [(WARN, "GPU", "no nvidia-smi — the LLM will run on CPU (slower drafts)")]
    return [(PASS, "GPU", smi.replace("\n", "; "))]


def check_ollama() -> list[Result]:
    url = os.environ.get("VEYRA_OLLAMA_URL", "http://localhost:11434").replace(
        "host.docker.internal", "localhost"
    )
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as resp:
            body = resp.read().decode()
    except (urllib.error.URLError, OSError) as exc:
        return [(WARN, "ollama", f"not reachable at {url} ({exc}) — needed from C4 on")]
    model = os.environ.get("VEYRA_LLM_MODEL", "qwen2.5:3b")
    have = model.split(":")[0] in body
    return [
        (PASS, "ollama", f"reachable at {url}"),
        (PASS if have else WARN, "llm model", f"{model} {'present' if have else 'not pulled'}"),
    ]


def check_kernel_bits() -> list[Result]:
    """chattr +i needs CAP_LINUX_IMMUTABLE in the archiver container (B2)."""
    if not shutil.which("chattr"):
        return [(WARN, "chattr", "not installed — set VEYRA_VAULT_CHATTR=0")]
    return [(PASS, "chattr", "present (vault immutability available)")]


def main() -> int:
    groups = (
        check_tools(),
        check_memory(),
        check_disk(),
        check_gpu(),
        check_ollama(),
        check_kernel_bits(),
    )
    results = [r for group in groups for r in group]
    width = max(len(check) for _, check, _ in results)
    for status, check, detail in results:
        print(f"{status:<5} {check:<{width}}  {detail}")
    failures = sum(1 for status, _, _ in results if status == FAIL)
    warnings = sum(1 for status, _, _ in results if status == WARN)
    print(f"\ndoctor: {failures} failed, {warnings} warnings")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
