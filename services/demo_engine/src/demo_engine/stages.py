"""Stage runner for the demo engine (B7).

A stage is the unit a hotkey triggers. Stages are **idempotent**: re-triggering one while it
runs is a no-op, and re-triggering a finished one runs it again — that is what makes the
demo script's "press it again" fallback safe.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

import httpx

from demo_engine.expectations import Evaluator, Outcome
from demo_engine.scenario import Scenario
from demo_engine.senders import TrafficSender
from veyra_common.settings import Settings

log = logging.getLogger(__name__)


class NoLiveKey(RuntimeError):
    """A stage needs the API key issued during onboarding and there is none yet."""


class StageRunner:
    def __init__(
        self,
        scenario: Scenario,
        sender: TrafficSender | None = None,
        cfg: Settings | None = None,
        evaluator: Evaluator | None = None,
    ) -> None:
        self.scenario = scenario
        self.cfg = cfg or Settings()
        self.sender = sender or TrafficSender(self.cfg)
        self.evaluator = evaluator or Evaluator(self.cfg)
        self._lock = threading.Lock()
        self._current = 0
        self._states: dict[int, str] = dict.fromkeys(scenario.stages, "idle")
        self._results: dict[int, list[Outcome]] = {number: [] for number in scenario.stages}
        self._errors: dict[int, str] = {}

    # ------------------------------------------------------------------ status
    def get_status(self) -> dict[str, Any]:
        with self._lock:
            current = self._current
            return {
                "stage": current,
                "state": self._states.get(current, "idle"),
                # Keyed by label so the console can show each expectation by name.
                "results": {o.label: o.ok for o in self._results.get(current, [])},
                "expects": [o.as_json() for o in self._results.get(current, [])],
                "error": self._errors.get(current),
                "all_states": dict(self._states),
            }

    def outcomes(self, number: int) -> list[Outcome]:
        """The evaluated expectations of one stage's last run."""
        with self._lock:
            return list(self._results.get(number, []))

    # ------------------------------------------------------------------ triggering
    def live_key(self) -> str:
        """The key issued during onboarding. Absent is loud: the stage cannot send."""
        url = f"{self.cfg.control_api_url.rstrip('/')}/internal/demo/last-key"
        try:
            response = httpx.get(url, timeout=5.0)
        except Exception as exc:
            raise NoLiveKey(f"control-api is unreachable at {url}: {exc}") from exc
        if response.status_code == 404:
            raise NoLiveKey(
                "no API key has been issued since the last reset; run the onboarding beat first"
            )
        response.raise_for_status()
        secret = response.json().get("secret")
        if not secret:
            raise NoLiveKey(f"{url} answered without a secret")
        return str(secret)

    def run_stage(self, number: int, live_key: str | None = None, block: bool = False) -> bool:
        """Start a stage. Returns False if it is already running (idempotence)."""
        if number not in self.scenario.stages:
            raise KeyError(f"stage {number} is not defined in scenario {self.scenario.name}")
        with self._lock:
            if self._states.get(number) == "running":
                log.info("stage %d is already running; ignoring the repeat trigger", number)
                return False
            self._current = number
            self._states[number] = "running"
            self._results[number] = []
            self._errors.pop(number, None)

        if block:
            self._execute(number, live_key)
            return True
        threading.Thread(
            target=self._execute, args=(number, live_key), daemon=True, name=f"stage-{number}"
        ).start()
        return True

    # ------------------------------------------------------------------ execution
    def _execute(self, number: int, live_key: str | None) -> None:
        stage = self.scenario.stages[number]
        log.info("stage %d (%s) starting", number, stage.title)
        # Seeded per stage, so one stage's randomness does not depend on what ran before.
        rng = random.Random(self.scenario.seed * 100 + number)
        failed_reason: str | None = None

        try:
            for position, action in enumerate(stage.actions):
                kind, cfg = next(iter(action.items()))
                self._run_action(
                    kind, cfg or {}, live_key, rng, f"stage {number} action {position}"
                )
        except Exception as exc:
            failed_reason = f"{type(exc).__name__}: {exc}"
            log.error("stage %d action failed: %s", number, failed_reason)

        outcomes = [self.evaluator.evaluate(clause) for clause in stage.expect]
        with self._lock:
            self._results[number] = outcomes
            if failed_reason:
                self._errors[number] = failed_reason
            self._states[number] = (
                "failed" if failed_reason or not all(o.ok for o in outcomes) else "done"
            )
        log.info(
            "stage %d finished: %s (%s)",
            number,
            self._states[number],
            ", ".join(f"{o.label}={'ok' if o.ok else 'FAIL'}" for o in outcomes)
            or "no expectations",
        )

    def _run_action(
        self,
        kind: str,
        cfg: dict[str, Any],
        live_key: str | None,
        rng: random.Random,
        where: str,
    ) -> None:
        if kind == "send":
            key = live_key
            if cfg.get("key") == "live" and not key:
                key = self.live_key()
            self.sender.dispatch_action({"send": cfg}, live_key=key, rng=rng)
        elif kind == "drift_flush":
            self._flush_drift()
        elif kind == "resend_if_no_drift":
            self._resend_if_no_drift(cfg, live_key, rng)
        elif kind == "prefill_onboarding":
            # The console reads the samples itself from `GET /scenario`; nothing to send.
            log.info("%s: onboarding samples are served to the console, not sent", where)
        else:
            raise ValueError(f"{where}: no handler for action {kind!r}")

    def _flush_drift(self) -> None:
        """Force the drift worker to emit for every open group (the stage-4 fallback)."""
        url = f"{self.cfg.drift_worker_url.rstrip('/')}/flush"
        response = httpx.post(url, timeout=10.0)
        response.raise_for_status()
        log.info("drift flush: %s", response.text[:200])

    def _resend_if_no_drift(
        self, cfg: dict[str, Any], live_key: str | None, rng: random.Random
    ) -> None:
        after_s = float(cfg.get("after_s", 10.0))
        source_id = str(cfg.get("source_id", "src_authsrv_01"))
        time.sleep(after_s)
        open_now, detail = self.evaluator.drift_open(source_id)
        if open_now:
            log.info("drift already open for %s (%s); nothing to resend", source_id, detail)
            return
        log.info("no drift for %s after %gs; resending the T3 shape", source_id, after_s)
        self.sender.dispatch_action(
            {
                "send": {
                    "via": "http_hec_event",
                    "key": "live",
                    "corpus": cfg.get("corpus", "authsrv_t3_failed.log"),
                    "count": int(cfg.get("count", 3)),
                    "over_s": float(cfg.get("over_s", 3)),
                }
            },
            live_key=live_key or self.live_key(),
            rng=rng,
        )
