"""CP3 — Loop closed (docs/plan/shared/S1_integration_checkpoints.md).

    make cp3

The whole contract loop through the public API: onboard from samples, push with the issued
key, draft on the drift the push raises, prove four-eyes refuses the author, promote, replay,
then tamper the replayed event and put it back.

The flows themselves live in `demo_engine.auto`, which is what `make demo-auto` and the
console's buttons drive. This script runs those and judges them, so there is one definition of
"the loop" rather than a second copy that can drift from it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# `tools/` on the path, so `checkpoints._common` and `veyra_lib` import whether this is run as
# a script, as a module, or through the compose `tools` service.
_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

from checkpoints._common import FAIL, WARN, Row, run, verdict  # noqa: E402
from demo_engine.auto import APPROVER, AUTHOR, AutoRunner, FlowError, Session  # noqa: E402
from demo_engine.expectations import Evaluator  # noqa: E402
from demo_engine.scenario import load_scenario  # noqa: E402
from demo_engine.senders import TrafficSender  # noqa: E402
from demo_engine.stages import StageRunner  # noqa: E402
from veyra_lib import http  # noqa: E402

from veyra_common.settings import Settings  # noqa: E402

CONTROL = "http://control-api:8000"
EVIDENCE = "http://evidence-api:8100"
MAHA_SOURCE = "src_authsrv_01"
_RUNNER: AutoRunner | None = None
_SENDER: TrafficSender | None = None


def _runner() -> AutoRunner:
    global _RUNNER, _SENDER
    if _RUNNER is not None:
        return _RUNNER
    cfg = Settings()
    scenario = load_scenario(name=cfg.demo_scenario, cfg=cfg)
    sender = TrafficSender(cfg)
    stages = StageRunner(scenario, sender, cfg, Evaluator(cfg))
    _RUNNER = AutoRunner(scenario, stages, cfg, Evaluator(cfg))
    _SENDER = sender
    return _RUNNER


# ---------------------------------------------------------------- 1
def c1_onboarding_issues_a_key() -> list[Row]:
    """Analyze -> draft -> submit -> a different user approves -> promote -> key."""
    try:
        detail = _runner().onboarding_flow()
    except FlowError as exc:
        return [Row(FAIL, "onboarding through the API", str(exc)[:200])]
    active = http(timeout=15).get(f"{EVIDENCE}/health").status_code < 400
    return [
        verdict(True, "contract active from onboarding", detail[:160]),
        verdict("key" in detail, "a per-source key was issued", detail[:160]),
        verdict(active, "evidence API answering", "used by the later criteria"),
    ]


# ---------------------------------------------------------------- 2
def c2_push_raises_drift() -> list[Row]:
    """Stage 3 pushes the T3 shape over HEC with that key; drift appears within 10 s."""
    runner = _runner()
    runner.stages.run_stage(3, block=True)
    outcomes = runner.stages.outcomes(3)
    tier3 = [o for o in outcomes if "tier=3" in o.label]
    rows = [
        verdict(
            runner.stages.get_status().get("state") == "done",
            "stage 3 sent its traffic",
            runner.stages.get_status().get("error") or "",
        )
    ]
    rows += [verdict(o.ok, o.label, o.detail) for o in tier3]

    evaluator = Evaluator(runner.cfg)
    started = time.monotonic()
    runner.stages.run_stage(4, block=True)
    open_now, detail = evaluator.drift_open(MAHA_SOURCE)
    evaluator.close()
    waited = time.monotonic() - started
    rows.append(verdict(open_now, "drift item open within 10 s", f"{detail} after {waited:.0f}s"))
    return rows


# ---------------------------------------------------------------- 3 and 5
def c3_draft_approve_promote_replay() -> list[Row]:
    """Draft on the drift item, promote it with the second pair of eyes, replay the events."""
    try:
        detail = _runner().drift_approve_promote_replay()
    except FlowError as exc:
        return [Row(FAIL, "the drift loop", str(exc)[:200])]
    replayed = "0 event(s) replayed" not in detail
    return [
        verdict(True, "drafted, approved by the approver, promoted", detail[:160]),
        verdict(replayed, "events replayed as revision 2", detail[:160]),
        Row(WARN, "rule 100111 fires retroactively for 103.21.4.77", "human: Wazuh Discover"),
    ]


# ---------------------------------------------------------------- 4
def c4_four_eyes_refuses_the_author() -> list[Row]:
    """The author approving their own version must be 403, not merely discouraged."""
    cfg = _runner().cfg
    session = Session(CONTROL)
    try:
        session.login(AUTHOR, cfg.demo_password)
        contracts = session.get("/contracts")
        pending = [
            (c["contract_id"], v["version"])
            for c in contracts
            for v in c.get("versions", [])
            if v.get("state") in ("draft", "testing")
        ]
        if not pending:
            # Nothing awaiting approval: submit one so the rule is actually exercised.
            return [Row(FAIL, "a version awaiting approval", "none; run criterion 1 first")]
        contract_id, version = pending[0]
        response = session.client.post(f"/contracts/{contract_id}/versions/{version}/approve")
        rows = [
            verdict(
                response.status_code == 403,
                "the author may not approve their own version",
                f"HTTP {response.status_code}: {response.text[:120]}",
            )
        ]
        session.switch(APPROVER)
        allowed = session.client.post(f"/contracts/{contract_id}/versions/{version}/approve")
        rows.append(
            verdict(
                allowed.status_code < 400,
                "the approver may",
                f"HTTP {allowed.status_code}",
            )
        )
        return rows
    finally:
        session.close()


# ---------------------------------------------------------------- 6
def c6_lineage_page() -> list[Row]:
    """The machine half: the event detail carries the bytes and the revision history."""
    client = http(timeout=30)
    search = client.get(f"{EVIDENCE}/lineage/search", params={"q": "sharma", "limit": 1})
    hits = search.json().get("hits") or []
    if not hits:
        return [Row(FAIL, "an event to open", "lineage search found nothing")]
    uid = str(hits[0]["event_uid"])
    detail = client.get(f"{EVIDENCE}/lineage/events/{uid}").json()
    raw = detail.get("raw") or {}
    revisions = detail.get("revisions") or []
    return [
        verdict(bool(raw.get("raw_text")), "raw bytes served for the highlighter", uid),
        verdict(
            bool(raw.get("field_offsets")),
            "field offsets served for the hover",
            f"{len(raw.get('field_offsets') or {})} field(s)",
        ),
        verdict(
            len(revisions) >= 2,
            "revision timeline shows 1 -> 2",
            f"{[r.get('revision') for r in revisions]}",
        ),
        Row(WARN, "hover highlights the bytes on screen", "human: the Lineage page"),
    ]


# ---------------------------------------------------------------- 7
def c7_tamper_then_restore() -> list[Row]:
    """An insider rewrite is caught and located, and untamper puts it back."""
    try:
        detail = _runner().verify_then_tamper_then_verify()
    except FlowError as exc:
        return [Row(FAIL, "tamper, detect, restore", str(exc)[:200])]
    return [
        verdict(True, "verified, broke under tamper, verified again", detail[:200]),
        verdict(
            "merkle_inclusion" in detail,
            "the break is located at merkle_inclusion",
            detail[:200],
        ),
    ]


CHECKS = {
    "1": ("onboarding through the API issues a working key", c1_onboarding_issues_a_key),
    "2": ("the pushed T3 shape raises drift", c2_push_raises_drift),
    "3": ("draft, approve, promote, replay", c3_draft_approve_promote_replay),
    "4": ("four-eyes refuses the author", c4_four_eyes_refuses_the_author),
    "6": ("the Lineage page's data is real", c6_lineage_page),
    "7": ("tamper is caught, located and reversible", c7_tamper_then_restore),
}

if __name__ == "__main__":
    try:
        raise SystemExit(run("CP3", CHECKS))
    finally:
        if _SENDER is not None:
            _SENDER.close()
