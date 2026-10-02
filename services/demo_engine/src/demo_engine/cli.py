"""The CLI behind ``make demo-reset``, ``demo-preflight``, ``demo-stage`` and ``demo-auto``.

Run from the host, so the defaults address the published ports rather than the compose
service names. The exit code is the answer: non-zero when a check FAILs, a stage's
expectations do not hold, or a reset does not complete inside its budget — that is what makes
these usable from a checklist or from CI.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Sequence
from typing import Any

import httpx

from demo_engine.auto import AutoRunner
from demo_engine.expectations import Evaluator
from demo_engine.preflight import PreflightChecker
from demo_engine.reset import ResetOrchestrator
from demo_engine.scenario import load_scenario
from demo_engine.senders import TrafficSender
from demo_engine.stages import StageRunner
from veyra_common.settings import Settings

log = logging.getLogger(__name__)

# From the host, every service is reachable on localhost through its published port.
HOST_OVERRIDES = {
    "control_api_url": "http://localhost:8000",
    "evidence_api_url": "http://localhost:8100",
    "drift_worker_url": "http://localhost:8206",
    "clickhouse_url": "http://localhost:8123",
    "kafka_bootstrap": "localhost:29092",
    "demo_edge_dmz_host": "localhost",
    "demo_edge_core_host": "localhost",
    "demo_gateway_url": "http://localhost:8088",
    "wazuh_indexer_url": "https://localhost:9200",
    "wazuh_manager_url": "https://localhost:55000",
    "wazuh_dashboard_url": "https://localhost:8443",
    "ollama_url": "http://localhost:11434",
    "demo_engine_url": "http://localhost:8300",
}


def host_settings(in_container: bool = False) -> Settings:
    cfg = Settings()
    return cfg if in_container else cfg.model_copy(update=HOST_OVERRIDES)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_engine.cli", description="VEYRA demo engine (B7)")
    parser.add_argument(
        "--in-container",
        action="store_true",
        help="address services by their compose names instead of localhost",
    )
    parser.add_argument("--scenario", default=None, help="scenario name (default: from settings)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("reset", help="put the system back to the pre-demo state")
    sub.add_parser("preflight", help="check the laptop is ready to demo")
    stage = sub.add_parser("stage", help="run one scenario stage")
    stage.add_argument("stage_num", type=int)
    auto = sub.add_parser("auto", help="drive the whole demo through the real APIs")
    auto.add_argument("count", type=int, nargs="?", default=1, help="runs (CP4 uses 10)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = host_settings(args.in_container)

    if args.command == "reset":
        return _reset(cfg, args.scenario)
    if args.command == "preflight":
        return _preflight(cfg)
    if args.command == "stage":
        return _stage(cfg, args.scenario, args.stage_num)
    if args.command == "auto":
        return _auto(cfg, args.scenario, args.count)
    return 2


def _reset(cfg: Settings, scenario_name: str | None) -> int:
    scenario = load_scenario(name=scenario_name or cfg.demo_scenario, cfg=cfg)
    result = _reset_via_engine(cfg, scenario.name)
    if result is None:
        # No engine to drive: run the steps here, and say what that costs. The baseline loop
        # lives inside the engine, so a local reset cannot pause it, and traffic landing
        # mid-wipe leaves rows in the index with no vault segment behind them.
        print(
            "the demo engine is not reachable; running the reset locally.\n"
            "  baseline traffic (if any) will NOT be paused, so this reset is not the one the"
            " demo uses."
        )
        result = ResetOrchestrator(None, cfg, scenario.name).execute_reset()
    print(
        f"\nreset {'OK' if result['ok'] else 'FAILED'} in {result['seconds']}s "
        f"(budget {cfg.demo_reset_budget_s}s)"
    )
    for step in result["steps"]:
        mark = "ok  " if step["ok"] else "FAIL"
        print(f"  {mark} {step['ms']:>6} ms  {step['name']}: {step['detail'][:70]}")
    if result["over_budget"]:
        print(f"\nover the {cfg.demo_reset_budget_s}s budget (B7 AC1)")
    return 0 if result["ok"] and not result["over_budget"] else 1


def _reset_via_engine(cfg: Settings, scenario_name: str) -> dict[str, Any] | None:
    """Ask the running engine to reset itself, and wait. None when it is not reachable.

    This is the path the console's Shift+R takes, so the CLI and the hotkey do the same
    thing — including step 1, which pauses the baseline the engine is producing.
    """
    base = cfg.demo_engine_url.rstrip("/")
    try:
        started = httpx.post(f"{base}/reset", timeout=10.0)
        started.raise_for_status()
    except Exception as exc:
        log.debug("demo engine not reachable at %s: %s", base, exc)
        return None

    deadline = time.monotonic() + cfg.demo_reset_budget_s * 3
    while time.monotonic() < deadline:
        try:
            status = httpx.get(f"{base}/reset/status", timeout=10.0).json()
        except Exception as exc:
            return {
                "ok": False,
                "seconds": 0,
                "over_budget": False,
                "steps": [
                    {"ok": False, "ms": 0, "name": "reset/status", "detail": f"{exc}"},
                ],
            }
        if not status.get("running"):
            return status
        time.sleep(1.0)
    return {
        "ok": False,
        "seconds": round(cfg.demo_reset_budget_s * 3, 1),
        "over_budget": True,
        "steps": [{"ok": False, "ms": 0, "name": "reset", "detail": "never finished"}],
    }


def _preflight(cfg: Settings) -> int:
    checks = PreflightChecker(cfg).check_all()
    print("\npreflight:")
    for check in checks:
        print(f"  [{check['status']:4}] {check['check']:<18} {check['detail']}")
    fails = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    print(f"\n{len(checks) - len(fails) - len(warns)} pass, {len(warns)} warn, {len(fails)} fail")
    return 1 if fails else 0


def _stage(cfg: Settings, scenario_name: str | None, number: int) -> int:
    scenario = load_scenario(name=scenario_name or cfg.demo_scenario, cfg=cfg)
    stage = scenario.stages.get(number)
    if stage is None:
        print(f"stage {number} is not in {scenario.name}; have {sorted(scenario.stages)}")
        return 2
    sender = TrafficSender(cfg)
    runner = StageRunner(scenario, sender, cfg, Evaluator(cfg))
    print(f"\nstage {number}: {stage.title}")
    try:
        runner.run_stage(number, block=True)
    finally:
        sender.close()
    status = runner.get_status()
    for outcome in status["expects"]:
        mark = "ok  " if outcome["ok"] else "FAIL"
        print(f"  {mark} {outcome['label']} ({outcome['seconds']}s): {outcome['detail'][:70]}")
    if status["error"]:
        print(f"  error: {status['error']}")
    print(f"\nstage {number}: {status['state']}")
    return 0 if status["state"] == "done" else 1


def _auto(cfg: Settings, scenario_name: str | None, count: int) -> int:
    scenario = load_scenario(name=scenario_name or cfg.demo_scenario, cfg=cfg)
    sender = TrafficSender(cfg)
    evaluator = Evaluator(cfg)
    runner = AutoRunner(scenario, StageRunner(scenario, sender, cfg, evaluator), cfg, evaluator)
    try:
        reports = runner.run(count)
    finally:
        sender.close()

    for index, report in enumerate(reports, start=1):
        print(f"\n===== run {index}/{count} =====")
        print(report.table())
    passed = sum(1 for report in reports if report.ok)
    print(f"\n{passed}/{count} run(s) passed")
    return 0 if passed == count else 1


if __name__ == "__main__":
    sys.exit(main())
