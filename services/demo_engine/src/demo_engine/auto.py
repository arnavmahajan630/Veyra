"""``make demo-auto``: drive the whole 3-minute demo through the real APIs (B7).

Each ``api:`` step in the scenario does what a human clicking would do, against the same
public endpoints the console calls — logging in, analyzing, drafting, switching user to
approve, promoting, replaying, verifying, tampering, verifying again. Nothing here reaches
into a database or calls an endpoint the console does not have; if a flow passes here but
fails on stage, the difference is the browser, not the API.

Every ``expect`` in the scenario is evaluated and printed as a PASS/FAIL table with timings.
CP4 runs this ten times in a row.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from demo_engine.expectations import Evaluator, Outcome
from demo_engine.scenario import Scenario
from demo_engine.stages import StageRunner
from veyra_common.settings import Settings

log = logging.getLogger(__name__)

AUTHOR = "author@maha"
APPROVER = "approver@veyra"
ADMIN = "admin@veyra"
MAHA_SOURCE = "src_authsrv_01"


class FlowError(RuntimeError):
    """An API flow could not complete, with what it was doing at the time."""


@dataclass
class StepReport:
    label: str
    ok: bool
    seconds: float
    detail: str = ""
    outcomes: list[Outcome] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ok": self.ok,
            "seconds": round(self.seconds, 2),
            "detail": self.detail,
            "expects": [o.as_json() for o in self.outcomes],
        }


@dataclass
class RunReport:
    ok: bool
    seconds: float
    steps: list[StepReport]

    def as_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "seconds": round(self.seconds, 2),
            "steps": [step.as_json() for step in self.steps],
        }

    def table(self) -> str:
        """The PASS/FAIL table `make demo-auto` prints."""
        width = max((len(step.label) for step in self.steps), default=10)
        lines = [f"{'RESULT':6} {'STEP'.ljust(width)}  {'TIME':>7}  DETAIL"]
        for step in self.steps:
            mark = "PASS" if step.ok else "FAIL"
            lines.append(
                f"{mark:6} {step.label.ljust(width)}  {step.seconds:6.1f}s  {step.detail[:70]}"
            )
            for outcome in step.outcomes:
                sub = "pass" if outcome.ok else "FAIL"
                lines.append(
                    f"  {sub:4} {outcome.label[: width + 2].ljust(width + 2)} "
                    f"{outcome.seconds:6.1f}s  {outcome.detail[:60]}"
                )
        verdict = "PASS" if self.ok else "FAIL"
        lines.append(f"\n{verdict} in {self.seconds:.1f}s")
        return "\n".join(lines)


class Session:
    """One logged-in console session, cookies and all."""

    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        # `transport` exists so the flows can be driven against a stub control API in tests;
        # in production it is None and httpx opens real connections.
        self.client = httpx.Client(
            base_url=self.base_url, timeout=60.0, follow_redirects=True, transport=transport
        )

    def close(self) -> None:
        self.client.close()

    def login(self, email: str, password: str) -> dict[str, Any]:
        return self.post("/auth/login", {"email": email, "password": password})

    def switch(self, email: str) -> dict[str, Any]:
        """The demo-mode user switcher, which is how four-eyes takes one click on stage."""
        return self.post("/auth/demo-switch", {"email": email})

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(path, params=params or None)
        self._check(response, f"GET {path}")
        return response.json()

    def post(self, path: str, body: Any = None) -> Any:
        response = self.client.post(path, json=body)
        self._check(response, f"POST {path}")
        return response.json() if response.content else {}

    @staticmethod
    def _check(response: httpx.Response, what: str) -> None:
        if response.status_code >= 400:
            raise FlowError(f"{what} -> {response.status_code}: {response.text[:300]}")


class AutoRunner:
    def __init__(
        self,
        scenario: Scenario,
        stage_runner: StageRunner,
        cfg: Settings | None = None,
        evaluator: Evaluator | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.scenario = scenario
        self.stages = stage_runner
        self.cfg = cfg or Settings()
        self.evaluator = evaluator or Evaluator(self.cfg)
        self.control = self.cfg.control_api_url.rstrip("/")
        self.evidence = self.cfg.evidence_api_url.rstrip("/")
        self.transport = transport

    # ------------------------------------------------------------------ the script
    def run_once(self) -> RunReport:
        started = time.monotonic()
        steps: list[StepReport] = []

        late: list[str] = []
        for entry in self.scenario.auto_script:
            at = float(entry.get("at", 0))
            wait = at - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
            step_started = time.monotonic()
            # A step that starts late is the demo running over its 3 minutes. Record it: a
            # run where every step passes but the clock says 4:30 is not a passing run.
            behind = (step_started - started) - at
            if behind > self.cfg.demo_auto_step_tolerance_s:
                late.append(f"{entry} started {behind:.0f}s behind its {at:g}s mark")

            if "stage" in entry:
                number = int(entry["stage"])
                label = f"stage {number}: {self.scenario.stages[number].title}"
                try:
                    # Blocking, so the expectations are evaluated before the next step.
                    self.stages.run_stage(number, block=True)
                    status = self.stages.get_status()
                    outcomes = self.stages.outcomes(number)
                    ok = status["state"] == "done"
                    detail = status.get("error") or ""
                except Exception as exc:
                    ok, detail, outcomes = False, f"{type(exc).__name__}: {exc}", []
                steps.append(
                    StepReport(label, ok, time.monotonic() - step_started, detail, list(outcomes))
                )
            elif "api" in entry:
                name = str(entry["api"])
                outcomes = []
                try:
                    detail = self.run_flow(name)
                    ok = True
                except Exception as exc:
                    ok, detail = False, f"{type(exc).__name__}: {exc}"
                if ok:
                    # An api step's `expect` clauses are checked against the live system, the
                    # same way a stage's are. Without them a flow only proved it did not raise.
                    outcomes = [
                        self.evaluator.evaluate(clause) for clause in entry.get("expect") or []
                    ]
                    ok = all(outcome.ok for outcome in outcomes if outcome.applicable)
                steps.append(
                    StepReport(
                        f"api {name}",
                        ok,
                        time.monotonic() - step_started,
                        detail,
                        list(outcomes),
                    )
                )
            else:
                steps.append(
                    StepReport(
                        str(entry), False, 0.0, "the step names neither a stage nor an api flow"
                    )
                )

        seconds = time.monotonic() - started
        budget = float(self.cfg.demo_auto_budget_s)
        if seconds > budget:
            late.append(f"the run took {seconds:.0f}s, over its {budget:g}s budget")
        if late:
            steps.append(StepReport("timing", False, 0.0, "; ".join(late)))
        return RunReport(all(s.ok for s in steps), seconds, steps)

    def run(self, times: int = 1) -> list[RunReport]:
        reports: list[RunReport] = []
        for attempt in range(1, times + 1):
            log.info("demo-auto run %d/%d", attempt, times)
            reports.append(self.run_once())
        return reports

    # ------------------------------------------------------------------ api flows
    def run_flow(self, name: str) -> str:
        flows = {
            "onboarding_flow": self.onboarding_flow,
            "drift_approve_promote_replay": self.drift_approve_promote_replay,
            "verify_then_tamper_then_verify": self.verify_then_tamper_then_verify,
        }
        try:
            flow = flows[name]
        except KeyError:
            raise FlowError(
                f"unknown api flow {name!r}; known: {', '.join(sorted(flows))}"
            ) from None
        return flow()

    def onboarding_flow(self) -> str:
        """Beat 2: analyze pasted samples, draft, submit, approve as a second person, get a key."""
        session = Session(self.control, self.transport)
        try:
            session.login(AUTHOR, self.cfg.demo_password)
            samples = self._onboarding_samples()
            source = self._ensure_maha_source(session)

            # Analyze streams SSE. It ends either with a draft id, or with a library pack that
            # matched well enough that there is no draft to wait for.
            kind, ref = self._analyze(session, source, samples)
            if kind == "library":
                version = session.post(
                    "/onboarding/use-library", {"source_id": source, "pack": ref}
                )
                provenance = f"library pack {ref}"
            else:
                draft = self._await_draft(session, ref)
                version = session.post(f"/drafts/{ref}/submit")
                provenance = f"draft {ref} ({len(draft.get('templates', []))} template(s))"
            contract_id = version["contract_id"]
            number = version["version"]

            # Four eyes: the author may not approve their own draft.
            session.switch(APPROVER)
            session.post(f"/contracts/{contract_id}/versions/{number}/approve")
            session.post(f"/contracts/{contract_id}/versions/{number}/promote")

            # Issuing a key needs a writer role (WRITERS = admin, pack_author); the approver
            # would get a 403, so hand the session back to the author first.
            session.switch(AUTHOR)
            key = session.post(f"/sources/{source}/keys", {"note": "demo-auto"})
            return (
                f"{contract_id}@{number} active from {provenance}; key {key.get('key_id')} issued"
            )
        finally:
            session.close()

    def _onboarding_samples(self) -> list[str]:
        """The samples stage 2 pre-fills, read from the scenario rather than hardcoded."""
        from demo_engine.senders import load_corpus

        stage = self.scenario.stages.get(2)
        for action in stage.actions if stage else []:
            if "prefill_onboarding" in action:
                refs = (action["prefill_onboarding"] or {}).get("samples", [])
                return [load_corpus(ref)[0] for ref in refs]
        raise FlowError("stage 2 defines no prefill_onboarding samples")

    def _ensure_maha_source(self, session: Session) -> str:
        """The source Beat 2 creates. Idempotent, so a re-run does not fail on conflict."""
        for source in session.get("/sources"):
            if source["id"] == MAHA_SOURCE:
                return MAHA_SOURCE
        session.post(
            "/sources",
            {
                "id": MAHA_SOURCE,
                "tenant_id": "t_maha_power",
                "name": "Auth Server",
                "vendor": "custom",
                "zone": "dmz",
                "transport": "http_push",
                "expected_eps": 2.0,
            },
        )
        return MAHA_SOURCE

    def _analyze(self, session: Session, source_id: str, samples: list[str]) -> tuple[str, str]:
        """POST /onboarding/analyze is an SSE stream; read it until it names an outcome.

        Returns ``("draft", draft_id)`` or ``("library", pack)``. The frames do not all carry
        the same shape — ``templates`` is a JSON list, the rest are objects — so anything that
        is not an object is skipped rather than attribute-accessed.
        """
        body = {"source_id": source_id, "samples": samples, "source_name": "Auth Server"}
        with session.client.stream("POST", "/onboarding/analyze", json=body) as response:
            if response.status_code >= 400:
                raise FlowError(f"analyze -> {response.status_code}: {response.read()[:300]!r}")
            import json as _json

            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = _json.loads(line[len("data:") :].strip() or "{}")
                if not isinstance(payload, dict):
                    continue
                if payload.get("message"):  # an `error` frame
                    raise FlowError(f"analyze failed: {str(payload['message'])[:200]}")
                draft_id = payload.get("draft_id") or payload.get("id")
                if draft_id:
                    return "draft", str(draft_id)
                pack = payload.get("library") or payload.get("matched")
                if pack:
                    return "library", str(pack)
        raise FlowError("the analyze stream ended without a draft id or a library pack")

    def _await_draft(
        self, session: Session, draft_id: str, timeout_s: float = 60.0
    ) -> dict[str, Any]:
        """Wait for the drafter, which may be live or falling back to its cache."""
        deadline = time.monotonic() + timeout_s
        last = "no state yet"
        while time.monotonic() < deadline:
            draft = session.get(f"/drafts/{draft_id}")
            state = draft.get("state")
            last = str(state)
            if state in ("ready", "drafted", "draft_ready"):
                return draft
            if state in ("failed", "refused"):
                raise FlowError(f"draft {draft_id} ended {state}: {draft.get('detail', '')[:200]}")
            time.sleep(1.0)
        raise FlowError(f"draft {draft_id} was still {last} after {timeout_s:g}s")

    def drift_approve_promote_replay(self) -> str:
        """Beat 4: take the open drift item through four eyes and replay the 8 events."""
        session = Session(self.control, self.transport)
        try:
            session.login(AUTHOR, self.cfg.demo_password)
            items = session.get("/drift", state="open", source_id=MAHA_SOURCE)
            items = items if isinstance(items, list) else items.get("items", [])
            if not items:
                raise FlowError(
                    f"no open drift item for {MAHA_SOURCE}; stage 4 should have made one"
                )
            item = items[0]
            drift_id, template_sig = item["drift_id"], item.get("template_sig")

            draft_id = str(session.post(f"/drift/{drift_id}/draft").get("draft_id"))
            self._await_draft(session, draft_id)
            version = session.post(f"/drafts/{draft_id}/submit")
            contract_id, number = version["contract_id"], version["version"]

            session.switch(APPROVER)
            session.post(f"/contracts/{contract_id}/versions/{number}/approve")
            session.post(f"/contracts/{contract_id}/versions/{number}/promote")

            job = session.post(
                "/replay",
                {
                    "contract_id": contract_id,
                    "template_sigs": [template_sig] if template_sig else [],
                    "source_id": MAHA_SOURCE,
                },
            )
            replayed = self._await_replay(session, str(job["job_id"]))
            return f"{contract_id}@{number} promoted; {replayed} event(s) replayed"
        finally:
            session.close()

    def _await_replay(self, session: Session, job_id: str, timeout_s: float = 60.0) -> int:
        deadline = time.monotonic() + timeout_s
        last = "unknown"
        while time.monotonic() < deadline:
            job = session.get(f"/replay/{job_id}")
            last = str(job.get("state"))
            if last in ("done", "completed"):
                # ReplayOut reports `published` and `normalized`; the count the demo narrates
                # is the one that came back out of the normalizer.
                return int(job.get("normalized") or job.get("published") or 0)
            if last in ("failed", "timed_out"):
                raise FlowError(f"replay {job_id} ended {last}: {job.get('detail', '')[:200]}")
            time.sleep(1.0)
        raise FlowError(f"replay {job_id} was still {last} after {timeout_s:g}s")

    def verify_then_tamper_then_verify(self) -> str:
        """Beat 5: verify green, tamper as an insider, verify red, then restore."""
        from demo_engine.tamper import TamperBridge

        # `_sealed_event` already verified it; that green report is the "before" state.
        uid, before = self._sealed_event()
        assert before["verified"]

        bridge = TamperBridge()
        bridge.tamper("insider_rewrite", uid)
        after = self._verify(uid)
        if after["verified"]:
            raise FlowError(
                f"{uid} still verified after an insider rewrite — tamper-evidence failed"
            )
        broke = [s["id"] for s in after["steps"] if not s["ok"] and not s.get("status")]

        bridge.untamper(uid)
        restored = self._verify(uid)
        if not restored["verified"]:
            raise FlowError(f"{uid} did not verify again after untampering")
        return f"{uid}: verified, broke at {broke} under tamper, verified again after restore"

    def _sealed_event(self, timeout_s: float = 120.0) -> tuple[str, dict[str, Any]]:
        """The newest matching event that is already sealed and signed, with its report.

        The newest hit is not necessarily verifiable yet: an event has to wait out its
        segment seal and its Merkle window, which is up to about 90 s. Taking hit[0] blindly
        made Beat 5 fail with "did not verify before tampering" whenever the replay had just
        happened — and tampering it would have been worse, because the window gets signed
        afterwards and the restore then no longer matches the signed leaf.
        """
        deadline = time.monotonic() + timeout_s
        last = "no event searched yet"
        while time.monotonic() < deadline:
            response = httpx.get(
                f"{self.evidence}/lineage/search",
                params={"q": self.scenario.tamper_query, "limit": 20},
                timeout=15.0,
            )
            response.raise_for_status()
            hits = response.json().get("hits") or []
            if not hits:
                last = f"lineage search for {self.scenario.tamper_query!r} found nothing"
            for hit in hits:
                uid = str(hit["event_uid"])
                report = self._verify(uid)
                if report["verified"]:
                    return uid, report
                pending = [s["id"] for s in report["steps"] if s.get("status") == "pending_seal"]
                last = (
                    f"{uid} is still waiting on {pending}"
                    if pending
                    else f"{uid} does not verify: "
                    + str([s["id"] for s in report["steps"] if not s["ok"]])
                )
            time.sleep(2.0)
        raise FlowError(f"no sealed, signed event to verify within {timeout_s:g}s ({last})")

    def _verify(self, uid: str) -> dict[str, Any]:
        response = httpx.get(f"{self.evidence}/evidence/verify/{uid}", timeout=30.0)
        response.raise_for_status()
        return dict(response.json())
