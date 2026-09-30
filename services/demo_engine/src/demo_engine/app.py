"""The demo engine's HTTP surface: IF-API-DEMO, served at ``/api/demo/*`` (B7).

Every route is demo-only. A bad scenario fails at start-up rather than being swallowed into
an empty one: a demo engine that answers happily while holding no stages is the worst thing
to discover with an audience in the room.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from demo_engine.auto import AutoRunner
from demo_engine.baseline import BaselineLoop
from demo_engine.expectations import Evaluator
from demo_engine.preflight import PreflightChecker
from demo_engine.reset import ResetOrchestrator
from demo_engine.scenario import Scenario, load_scenario
from demo_engine.senders import TrafficSender
from demo_engine.stages import NoLiveKey, StageRunner
from demo_engine.tamper import TamperBridge, TamperUnavailable
from veyra_common.settings import Settings

log = logging.getLogger(__name__)


class TamperRequest(BaseModel):
    mode: str = "insider_rewrite"
    event_uid: str | None = None


class UntamperRequest(BaseModel):
    event_uid: str | None = None


class HotkeyPing(BaseModel):
    """The console reports its registered demo hotkeys, so preflight can check them."""

    combos: list[str] = []


def create_app(
    scenario_path: Any = None,
    cfg: Settings | None = None,
    *,
    start_baseline: bool = True,
) -> FastAPI:
    settings_ = cfg or Settings()
    scenario: Scenario = load_scenario(scenario_path, name=settings_.demo_scenario, cfg=settings_)

    sender = TrafficSender(settings_)
    baseline = BaselineLoop(scenario.baseline, sender, seed=scenario.seed)
    evaluator = Evaluator(settings_)
    stages = StageRunner(scenario, sender, settings_, evaluator)
    reset = ResetOrchestrator(baseline, settings_, scenario.name)
    tamper = TamperBridge()
    hotkeys: dict[str, list[str]] = {}
    preflight = PreflightChecker(settings_, baseline, hotkeys_seen=lambda: bool(hotkeys))
    auto = AutoRunner(scenario, stages, settings_, evaluator)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        if start_baseline:
            baseline.start()
        yield
        baseline.stop()
        sender.close()

    api = FastAPI(
        title="VEYRA demo engine",
        version="0.1.0",
        description="Scenario stages, reset, preflight and the tamper lab (IF-API-DEMO).",
        lifespan=lifespan,
    )
    api.state.scenario = scenario
    api.state.sender = sender
    api.state.baseline = baseline
    api.state.stages = stages
    api.state.reset = reset
    api.state.preflight = preflight
    api.state.tamper = tamper
    api.state.auto = auto

    @api.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "demo_engine",
            "scenario": scenario.name,
            "stages": sorted(scenario.stages),
        }

    @api.get("/scenario")
    def get_scenario() -> dict[str, Any]:
        """The loaded scenario, including what each stage's onboarding pre-fill holds."""
        return {
            "scenario": scenario.name,
            "seed": scenario.seed,
            "baseline": [
                {"name": s.name, "via": s.via, "corpus": s.corpus, "eps": s.eps}
                for s in scenario.baseline
            ],
            "stages": {
                str(number): {
                    "title": stage.title,
                    "actions_count": len(stage.actions),
                    "expects": [_expect_label(clause) for clause in stage.expect],
                    "samples": _samples_of(stage),
                }
                for number, stage in sorted(scenario.stages.items())
            },
        }

    @api.post("/stage/{stage_num}")
    def trigger_stage(stage_num: int) -> dict[str, Any]:
        if stage_num not in scenario.stages:
            raise HTTPException(
                status_code=400,
                detail=f"no stage {stage_num} in {scenario.name}: have {sorted(scenario.stages)}",
            )
        try:
            started = stages.run_stage(stage_num)
        except NoLiveKey as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # False means it was already running: a double press must not double-send.
        return {"ok": started, "stage": stage_num, "already_running": not started}

    @api.get("/stage/status")
    def stage_status() -> dict[str, Any]:
        return stages.get_status()

    @api.post("/reset")
    def start_reset() -> dict[str, Any]:
        """Kick the reset off and return at once; poll ``/reset/status`` for progress."""
        if not reset.start():
            raise HTTPException(status_code=409, detail="a reset is already running")
        return {"started": True, "budget_s": settings_.demo_reset_budget_s}

    @api.get("/reset/status")
    def reset_status() -> dict[str, Any]:
        return reset.status()

    @api.get("/preflight")
    def get_preflight() -> list[dict[str, str]]:
        return preflight.check_all()

    @api.post("/tamper")
    def apply_tamper(body: TamperRequest) -> dict[str, Any]:
        try:
            return tamper.tamper(body.mode, body.event_uid)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TamperUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.post("/untamper")
    def apply_untamper(body: UntamperRequest | None = None) -> dict[str, Any]:
        try:
            return tamper.untamper((body.event_uid if body else None) or None)
        except TamperUnavailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @api.get("/tamper/active")
    def active_tampers() -> dict[str, Any]:
        return {"active": tamper.active(), "modes": list(tamper.modes())}

    @api.get("/baseline")
    def baseline_status() -> dict[str, Any]:
        return baseline.snapshot()

    @api.post("/baseline/pause")
    def pause_baseline() -> dict[str, Any]:
        baseline.pause()
        return baseline.snapshot()

    @api.post("/baseline/resume")
    def resume_baseline() -> dict[str, Any]:
        baseline.resume()
        return baseline.snapshot()

    @api.post("/hotkeys")
    def report_hotkeys(body: HotkeyPing) -> dict[str, Any]:
        """The console pings this on mount; preflight checks that it happened."""
        hotkeys["console"] = list(body.combos)
        return {"ok": True, "combos": hotkeys["console"]}

    return api


def _expect_label(clause: dict[str, Any]) -> str:
    """Name an expectation the same way the stage runner's results key it."""
    from demo_engine.expectations import Outcome

    return Outcome(clause, False, "", 0.0).label


def _samples_of(stage: Any) -> list[str]:
    """The onboarding samples this stage pre-fills, resolved to their actual lines."""
    from demo_engine.senders import load_corpus

    for action in stage.actions:
        if "prefill_onboarding" in action:
            refs = (action["prefill_onboarding"] or {}).get("samples", [])
            return [load_corpus(ref)[0] for ref in refs]
    return []
