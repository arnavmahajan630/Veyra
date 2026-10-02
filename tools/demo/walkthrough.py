"""Guided, narrated end-to-end demo of what Veyra does today (./veyra.sh demo).

Runs inside the compose ``tools`` container. Each beat says what it proves, does it through
the real entry points (syslog sockets, the HEC gateway, control-api REST, evidence-api), and
shows the evidence. A beat that fails reports why and the walkthrough carries on.

    python tools/demo/walkthrough.py                 # pause between beats
    python tools/demo/walkthrough.py --auto          # no pauses
    python tools/demo/walkthrough.py --beat 5        # one beat

Beats 3-5 (onboarding, drift and evidence) drive ``demo_engine.auto.AutoRunner`` directly,
the same flows ``make demo-auto`` and CP4 use, instead of reimplementing the HTTP
choreography here — so there is one assertion path, not two that can drift apart.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import traceback
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import httpx  # noqa: E402
from demo_engine.auto import AutoRunner, FlowError  # noqa: E402
from demo_engine.expectations import Evaluator  # noqa: E402
from demo_engine.scenario import load_scenario  # noqa: E402
from demo_engine.senders import TrafficSender  # noqa: E402
from demo_engine.stages import StageRunner  # noqa: E402
from veyra_lib import (  # noqa: E402
    Results,
    bold,
    collect,
    cyan,
    dim,
    green,
    http,
    pause,
    red,
    tail_consumer,
    wait_until,
    yellow,
)

from veyra_common.framing import split_lines  # noqa: E402
from veyra_common.settings import settings  # noqa: E402

CONTROL = "http://control-api:8000"
GATEWAY = "http://ingest-gateway:8088"
EVIDENCE = "http://evidence-api:8100"
DRIFT = "http://drift-worker:8206"
DEMO_ENGINE = "http://demo-engine:8300"
CORPUS = REPO / "demo" / "corpus"
AUTHSRV = "src_authsrv_01"
PASSWORD = settings.demo_password

STATE: dict[str, Any] = {}  # handed from beat to beat (event uids, key secret, contract ids)


# ---------------------------------------------------------------- helpers
def headline(n: int, title: str, proves: str) -> None:
    print("\n" + bold(cyan(f"Beat {n}  {title}")))
    print(dim(f"  Proves: {proves}"))


def say(text: str) -> None:
    print(f"  {text}", flush=True)


def run_tool(args: list[str], *, show: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, *args], cwd=REPO, capture_output=True, text=True, timeout=600
    )
    if show:
        for line in (result.stdout + result.stderr).strip().splitlines():
            print(dim(f"    | {line}"))
    return result


def login(email: str) -> httpx.Client:
    client = http(timeout=30)
    response = client.post(f"{CONTROL}/auth/login", json={"email": email, "password": PASSWORD})
    response.raise_for_status()
    return client


def corpus_events(name: str) -> list[str]:
    data = (CORPUS / name).read_bytes()
    return [f.raw.decode("utf-8", "replace") for f in split_lines(data)]


def tier_of(payload: dict[str, Any]) -> int | None:
    return (payload.get("ulpf") or {}).get("tier")


def summarize(found: list[tuple[str, dict[str, Any]]]) -> None:
    """Normalized events by source, tier and topic."""
    by = Counter(
        ((p.get("ulpf") or {}).get("source_id", "?"), tier_of(p), topic)
        for topic, p in found
        if topic.startswith("norm.")
    )
    for (source, tier, topic), count in sorted(
        by.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)
    ):
        say(f"  {count:>4} x {source:<18} tier {tier}  -> {topic}")


def step(results: Results, name: str, ok: bool, detail: str = "") -> bool:
    return results.check(name, ok, detail)


def run_flow(results: Results, auto: AutoRunner, name: str, label: str) -> str | None:
    """Drive one of the demo engine's api flows and report it exactly as ``demo-auto`` would.

    This is the one assertion path: the same ``AutoRunner.run_flow`` that ``make demo-auto``
    and CP4 use, so the walkthrough cannot drift from what the console's hotkeys exercise.
    """
    entry: dict[str, Any] = next((e for e in auto.scenario.auto_script if e.get("api") == name), {})
    try:
        detail = auto.run_flow(name)
    except FlowError as exc:
        step(results, label, False, str(exc)[:200])
        return None
    say(detail)
    ok = step(results, label, True, detail[:160])
    for clause in entry.get("expect") or []:
        outcome = auto.evaluator.evaluate(clause)
        passed = outcome.ok or not outcome.applicable
        step(results, f"  expect: {outcome.label}", passed, outcome.detail[:160])
        ok = ok and passed
    return detail if ok else None


# ---------------------------------------------------------------- beat 1
def beat_ingest(results: Results) -> None:
    headline(
        1,
        "Ingest over syslog",
        "devices send ordinary syslog; every line is stamped, queued and translated",
    )
    consumer = tail_consumer(("raw.", "norm."))
    try:
        say("Linux server -> core edge over syslog UDP:")
        run_tool(["demo/tools/send_syslog.py", "--file", "linux_sshd.log"])
        # Over the real DMZ listener, syslog header and all. The seeded contract declares the
        # syslog layer as `optional`, so the same contract covers both this and bare CEF from
        # an HTTP push — which is why this no longer has to be published straight to Kafka.
        say("vendor firewall (CEF) -> DMZ edge over syslog TCP:")
        run_tool(["demo/tools/send_syslog.py", "--file", "acme_ngfw_cef.log"])
        say("waiting for the normalizer ...")
        found = collect(consumer, lambda t, p: True, timeout=25, want=10_000)
    finally:
        consumer.close()
    raw = [p for t, p in found if t.startswith("raw.")]
    norm = [(t, p) for t, p in found if t.startswith("norm.")]
    STATE["sshd_uids"] = [p["event_uid"] for p in raw if p.get("source_id") == "src_lnx_core_07"]
    say(f"{len(raw)} envelopes stamped on raw.*, {len(norm)} normalized events on norm.*")
    summarize(norm)
    sample = next((p for t, p in norm if t == "norm.iam" and tier_of(p) == 1), None)
    if sample:
        ulpf = sample["ulpf"]
        say(bold("one normalized sshd event:"))
        say(
            f"  class_uid {sample.get('class_uid')}  user {sample.get('user', {}).get('name')}  "
            f"src {sample.get('src_endpoint', {}).get('ip')}  status_id {sample.get('status_id')}"
        )
        say(
            f"  contract {(ulpf.get('contract') or {}).get('id')}"
            f"@{(ulpf.get('contract') or {}).get('version')}  "
            f"tier {ulpf['tier']}  raw {ulpf['raw_ref']['topic']}:{ulpf['raw_ref']['offset']}"
        )
        offsets = ulpf.get("field_offsets", {})
        say(
            f"  byte offsets for {len(offsets)} fields, e.g. "
            + ", ".join(f"{k}={v[0]}..{v[1]}" for k, v in list(offsets.items())[:3])
        )
    step(
        results, "sshd + CEF lines reached norm.* at tier 1", any(tier_of(p) == 1 for _, p in norm)
    )


# ---------------------------------------------------------------- beat 2
def beat_messy(results: Results) -> None:
    headline(
        2,
        "Messy and garbage logs",
        "nothing is dropped: unknown shapes are tier 3 with observables, garbage is tier 4",
    )
    consumer = tail_consumer(("raw.", "norm."))
    try:
        run_tool(
            ["demo/tools/send_syslog.py", "--file", "ot_historian.log", "--file", "garbage.bin"]
        )
        found = collect(consumer, lambda t, p: True, timeout=25, want=10_000)
    finally:
        consumer.close()
    norm = [(t, p) for t, p in found if t.startswith("norm.")]
    summarize(norm)
    tier3s = [p for _, p in norm if tier_of(p) == 3]
    tier3 = next((p for p in tier3s if p.get("observables")), tier3s[0] if tier3s else None)
    if tier3:
        say(bold("one tier-3 event (no contract matched):"))
        hint = tier3["ulpf"].get("class_hint")
        say(
            "  observables: "
            + json.dumps(tier3.get("observables", [])[:4], ensure_ascii=False)[:160]
        )
        say(f"  class hint: {hint}   (a hint only; class_uid stays {tier3.get('class_uid')})")
        say(f"  offsets: {list(tier3['ulpf'].get('field_offsets', {}).items())[:3]}")
    tiers = {tier_of(p) for _, p in norm}
    step(
        results,
        "unknown lines delivered, none dropped",
        bool(norm) and bool(tiers & {3, 4}),
        f"tiers seen: {sorted(t for t in tiers if t)}",
    )


# ---------------------------------------------------------------- beat 3
def push(secret: str, events: list[str]) -> int:
    sent = 0
    with http(timeout=15) as client:
        for event in events:
            r = client.post(
                f"{GATEWAY}/services/collector/event",
                headers={"Authorization": f"Splunk {secret}"},
                json={"event": event},
            )
            sent += r.status_code == 200
    return sent


def beat_onboard(results: Results, auto: AutoRunner) -> None:
    headline(
        3,
        "Onboard a new organisation's source",
        "plug-and-play onboarding with two-person approval, "
        "then push over HTTP with a per-source key",
    )
    # The whole four-eyes choreography (register, paste samples, draft or match a library
    # pack, switch user, approve, promote, issue a key) is one call into the same
    # `AutoRunner.onboarding_flow` that `make demo-auto` and CP4 drive, so there is exactly
    # one place that implements it.
    if (
        run_flow(
            results,
            auto,
            "onboarding_flow",
            "paste samples -> draft/library match -> four-eyes approve -> promote -> key issued",
        )
        is None
    ):
        return

    author = login("author@maha")
    try:
        card = author.post(
            f"{CONTROL}/sources/{AUTHSRV}/keys", json={"note": "walkthrough display traffic"}
        )
    finally:
        author.close()
    if not step(
        results,
        "a second key is issued to push and show live traffic",
        card.status_code == 201,
        card.text[:120] if card.status_code != 201 else card.json()["key_id"],
    ):
        return
    secret = card.json()["secret"]
    STATE["secret"] = secret
    say(dim(f"curl example: {card.json().get('curl_example', '')[:110]}"))
    ready = wait_until(
        lambda: push(secret, ["key warm-up probe"]) == 1,
        timeout=30,
        label="waiting for the gateway to see the key",
    )
    step(results, "gateway accepts the new key", bool(ready))

    consumer = tail_consumer(("norm.",))
    try:
        ok_events = (
            corpus_events("authsrv_t1_ok.log")[:4] + corpus_events("authsrv_t2_session.log")[:4]
        )
        say(f"pushed {push(secret, ok_events)} T1/T2 events over HTTP")
        found = collect(
            consumer,
            lambda t, p: (p.get("ulpf") or {}).get("source_id") == AUTHSRV,
            timeout=25,
            want=len(ok_events),
        )
    finally:
        consumer.close()
    summarize(found)
    step(results, "known shapes arrive at tier 1", any(tier_of(p) == 1 for _, p in found))


# ---------------------------------------------------------------- beat 4
def beat_drift(results: Results, auto: AutoRunner) -> None:
    headline(
        4,
        "A new log shape: drift -> draft -> approve -> replay",
        "Veyra learns an unseen format safely; history is re-translated as revision 2",
    )
    secret = STATE.get("secret")
    if not secret:
        say(yellow("no API key from beat 3; run the beats in order"))
        results.report("WARN", "drift beat skipped (needs beat 3)")
        return
    t3 = corpus_events("authsrv_t3_failed.log")[:8]
    consumer = tail_consumer(("norm.",))
    try:
        say(f"pushed {push(secret, t3)} T3 'FAILED login' events (multi-line, with stack traces)")
        found = collect(
            consumer,
            lambda t, p: (p.get("ulpf") or {}).get("source_id") == AUTHSRV,
            timeout=25,
            want=len(t3),
        )
    finally:
        consumer.close()
    summarize(found)
    step(
        results,
        "the unseen shape arrives at tier 3 (brute force invisible to the SIEM)",
        any(tier_of(p) == 3 for _, p in found),
    )

    author = login("author@maha")
    try:

        def drift_item() -> dict[str, Any] | None:
            with contextlib.suppress(httpx.HTTPError):  # flush just speeds the worker up
                http(timeout=5).post(f"{DRIFT}/flush")
            items = author.get(f"{CONTROL}/drift", params={"source_id": AUTHSRV}).json()
            return next(
                (i for i in items if i.get("state") in ("open", "draft_ready", "drafting")), None
            )

        item = wait_until(drift_item, timeout=60, interval=2, label="waiting for the drift worker")
        if not step(
            results,
            "drift item raised for the new shape",
            bool(item),
            f"count {item.get('count')}: {item.get('drain_template', '')[:70]}"
            if item
            else "none within 60 s",
        ):
            return
    finally:
        author.close()

    # Draft, four-eyes approve, promote and replay are one call into the same
    # `AutoRunner.drift_approve_promote_replay` that `make demo-auto` and CP4 drive.
    consumer = tail_consumer(("norm.",))
    try:
        if (
            run_flow(
                results,
                auto,
                "drift_approve_promote_replay",
                "draft the drifted shape -> four-eyes approve -> promote -> replay the T3 events",
            )
            is None
        ):
            return
        found = collect(
            consumer, lambda t, p: (p.get("ulpf") or {}).get("revision") == 2, timeout=40, want=8
        )
    finally:
        consumer.close()
    summarize(found)
    step(
        results,
        "replayed events are revision 2 at tier 1",
        bool(found) and all(tier_of(p) == 1 for _, p in found),
        f"{len(found)} events",
    )


# ---------------------------------------------------------------- beat 5
def verify(uid: str) -> dict[str, Any]:
    return http(timeout=30).get(f"{EVIDENCE}/evidence/{uid}/verify").json()


def show_report(report: dict[str, Any]) -> None:
    for s in report.get("steps", []):
        mark = green("ok  ") if s["ok"] else (yellow("n/a ") if s.get("status") else red("FAIL"))
        say(f"  {mark} {s['id']:<18} {dim(s.get('detail', '')[:70])}")


def beat_evidence(results: Results, auto: AutoRunner) -> None:
    headline(
        5,
        "Evidence: verify, tamper, detect",
        "stored bytes are provably untouched, and an insider's edit is caught and located",
    )
    say(bold(f"picking the newest sealed, signed event matching {auto.scenario.tamper_query!r}"))
    # Pick, verify, tamper as an insider, verify red, untamper, verify green: one call into
    # the same `AutoRunner.verify_then_tamper_then_verify` that `make demo-auto` and CP4
    # drive, so Beat 5 cannot silently diverge from what the console's Shift+T exercises.
    detail = run_flow(
        results,
        auto,
        "verify_then_tamper_then_verify",
        "verify green -> insider tamper -> verify red, located -> untamper -> verify green",
    )
    if detail is None:
        return
    uid = detail.split(":", 1)[0]
    say(f"event {uid}")
    show_report(verify(uid))
    audit = run_tool(["tools/ledger_audit.py"])
    step(results, "ledger audit: every signed root and link checks out", audit.returncode == 0)


# ---------------------------------------------------------------- main
BEATS: dict[int, Callable[..., None]] = {
    1: beat_ingest,
    2: beat_messy,
    3: beat_onboard,
    4: beat_drift,
    5: beat_evidence,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--auto", action="store_true", help="no pauses between beats")
    parser.add_argument("--beat", type=int, action="append", choices=sorted(BEATS))
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="keep the control plane as is (default: reset it to the seeded demo world first)",
    )
    args = parser.parse_args(argv)
    beats = args.beat or sorted(BEATS)
    if not args.no_reset and ({3, 4} & set(beats)):
        # Beats 3-4 onboard src_authsrv_01 and promote its contract; start from the seed world
        # so a second run behaves exactly like the first.
        # The demo engine's reset is the whole world (control plane, Kafka, ClickHouse, the
        # vault, the sinks, the drift state) and it pauses its own baseline traffic first. Fall
        # back to the control-plane-only reset when the engine is not running.
        try:
            started = http(timeout=15).post(f"{DEMO_ENGINE}/reset")
            started.raise_for_status()
            status = (
                wait_until(
                    lambda: (
                        (st := http(timeout=15).get(f"{DEMO_ENGINE}/reset/status").json())
                        and not st.get("running")
                        and st
                    ),
                    timeout=300,
                    interval=2,
                    label="resetting",
                )
                or {}
            )
            print(
                dim(
                    f"  reset to the seeded demo world in {status.get('seconds')}s "
                    f"(ok={status.get('ok')})"
                )
            )
        except Exception as exc:
            print(dim(f"  demo engine unavailable ({type(exc).__name__}); control plane only"))
            reset = http(timeout=120).post(
                f"{CONTROL}/internal/reset", json={"scenario": "sih_main"}
            )
            print(dim(f"  control plane reset (HTTP {reset.status_code})"))

    print(bold("\nVeyra guided demo") + dim(f"  (profile {settings.profile})"))
    print(
        dim(
            "  Open the console at http://localhost:8080 and the Kafka UI at "
            "http://localhost:8085 to watch along."
        )
    )

    # Beats 3-5 drive the same AutoRunner flows `make demo-auto` and CP4 use (B7), so there
    # is exactly one place that implements onboarding/drift/evidence instead of two.
    scenario = load_scenario(name=settings.demo_scenario, cfg=settings)
    sender = TrafficSender(settings)
    evaluator = Evaluator(settings)
    stage_runner = StageRunner(scenario, sender, settings, evaluator)
    auto_runner = AutoRunner(scenario, stage_runner, settings, evaluator)

    results = Results()
    try:
        for n in beats:
            try:
                fn = BEATS[n]
                fn(results, auto_runner) if n in (3, 4, 5) else fn(results)
            except Exception as exc:
                results.report("FAIL", f"beat {n} crashed: {type(exc).__name__}: {exc}")
                traceback.print_exc(limit=2)
            pause(args.auto)
    finally:
        sender.close()
        evaluator.close()

    print(bold("\nWhat you just saw vs. what is not built yet"))
    say(
        "shown live: syslog + HTTP ingest, tiers 1-4, byte offsets, delivery to Wazuh, "
        "onboarding with four-eyes, drift, drafting,"
    )
    say(
        "            promotion, replay as revision 2, sealed evidence with verify, "
        "tamper detection, ledger audit"
    )
    say(
        "also built:  the demo engine (/demo, Shift+1..6) and the console's Lineage and "
        "Evidence pages — open http://localhost:8080/demo and /evidence to drive them"
    )
    say(yellow("declared:   verify's 8th step (immudb anchoring) is not implemented in this"))
    say(yellow("            build; it reports `not_implemented` and 7 steps decide the verdict"))
    return results.summary()


if __name__ == "__main__":
    raise SystemExit(main())
