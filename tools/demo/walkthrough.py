"""Guided, narrated end-to-end demo of what Veyra does today (./veyra.sh demo).

Runs inside the compose ``tools`` container. Each beat says what it proves, does it through
the real entry points (syslog sockets, the HEC gateway, control-api REST, evidence-api), and
shows the evidence. A beat that fails reports why and the walkthrough carries on.

    python tools/demo/walkthrough.py                 # pause between beats
    python tools/demo/walkthrough.py --auto          # no pauses
    python tools/demo/walkthrough.py --beat 5        # one beat
    python tools/demo/walkthrough.py --draft-mode live   # let the AI draft (else: heuristic)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
import time
import traceback
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import httpx  # noqa: E402
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
        # The seeded firewall contract parses bare CEF (no syslog layer), so its events are
        # stamped exactly as the edge would and published straight to raw.acme_ngfw.
        say("vendor firewall (CEF) -> raw.acme_ngfw, stamped as the DMZ edge would:")
        run_tool(["demo/tools/fake_raw.py", "--file", "acme_ngfw_cef.log", "--eps", "0"])
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
def sse_events(response: httpx.Response) -> list[tuple[str, dict[str, Any]]]:
    events, name = [], "message"
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            try:
                events.append((name, json.loads(line[5:].strip())))
            except ValueError:
                events.append((name, {"raw": line[5:].strip()}))
    return events


def promote_version(results: Results, author: httpx.Client, contract_id: str, version: int) -> bool:
    """Four-eyes: the author may not approve; approver@veyra approves and promotes."""
    url = f"{CONTROL}/contracts/{contract_id}/versions/{version}"
    own = author.post(f"{url}/approve")
    step(
        results,
        f"author approving {contract_id}@{version} is refused (four-eyes)",
        own.status_code == 403,
        f"HTTP {own.status_code}",
    )
    approver = login("approver@veyra")
    try:
        approved = approver.post(f"{url}/approve")
        step(
            results,
            f"approver@veyra approves {contract_id}@{version}",
            approved.status_code == 200,
            f"HTTP {approved.status_code}",
        )
        promoted = approver.post(f"{url}/promote")
        ok = promoted.status_code == 200
        step(
            results,
            f"{contract_id}@{version} promoted to active",
            ok,
            promoted.json().get("state", promoted.text[:80]) if ok else promoted.text[:120],
        )
    finally:
        approver.close()
    return ok


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


def beat_onboard(results: Results, draft_mode: str) -> None:
    headline(
        3,
        "Onboard a new organisation's source",
        "plug-and-play onboarding with two-person approval, "
        "then push over HTTP with a per-source key",
    )
    author = login("author@maha")
    source = {
        "id": AUTHSRV,
        "tenant_id": "t_maha_power",
        "name": "Auth Server",
        "vendor": "custom",
        "zone": "dmz",
        "transport": "http_push",
    }
    created = author.post(f"{CONTROL}/sources", json=source)
    exists = (
        created.status_code == 409 or author.get(f"{CONTROL}/sources/{AUTHSRV}").status_code == 200
    )
    step(
        results,
        f"author@maha registers {AUTHSRV}",
        created.status_code == 201 or exists,
        f"HTTP {created.status_code}",
    )

    contract = author.get(f"{CONTROL}/contracts/authsrv")
    if contract.status_code == 200 and any(
        v.get("state") == "active" for v in contract.json().get("versions", [])
    ):
        say(
            dim(
                "authsrv already has an active contract (a previous run); "
                "skipping the onboarding draft"
            )
        )
    else:
        samples = (
            corpus_events("authsrv_t1_ok.log")[:3] + corpus_events("authsrv_t2_session.log")[:3]
        )
        say(f"pasting {len(samples)} sample lines (T1 logins + T2 sessions) into onboarding ...")
        with author.stream(
            "POST",
            f"{CONTROL}/onboarding/analyze",
            json={"source_id": AUTHSRV, "samples": samples, "mode": draft_mode},
            timeout=180,
        ) as response:
            events = sse_events(response)
        names = [name for name, _ in events]
        templates = next((d for n, d in events if n == "templates"), None)
        say(f"analysis events: {', '.join(dict.fromkeys(names))}")
        if isinstance(templates, (list, dict)):
            count = len(
                templates if isinstance(templates, list) else templates.get("templates", [])
            )
            say(f"{count} message shapes found")
        done = next((d for n, d in events if n == "done"), {})
        draft_id = done.get("draft_id")
        if not step(
            results,
            "onboarding produced a draft contract",
            bool(draft_id),
            draft_id or json.dumps(done)[:120],
        ):
            return
        draft = author.get(f"{CONTROL}/drafts/{draft_id}").json()
        say(
            f"draft state {draft.get('state')}; "
            f"verification: {json.dumps(draft.get('verification'))[:160]}"
        )
        submitted = author.post(f"{CONTROL}/drafts/{draft_id}/submit")
        if not step(
            results,
            "draft submitted as a new contract version",
            submitted.status_code == 201,
            submitted.text[:120] if submitted.status_code != 201 else "",
        ):
            return
        version = submitted.json()
        STATE["authsrv_v1"] = version["version"]
        promote_version(results, author, version["contract_id"], version["version"])

    card = author.post(f"{CONTROL}/sources/{AUTHSRV}/keys", json={})
    if not step(
        results,
        "API key issued for the source",
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
def beat_drift(results: Results, draft_mode: str) -> None:
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
    started = author.post(f"{CONTROL}/drift/{item['drift_id']}/draft", json={"mode": draft_mode})
    draft_id = started.json().get("draft_id") if started.status_code == 202 else None
    if not step(
        results,
        f"draft started ({draft_mode})",
        bool(draft_id),
        draft_id or started.text[:120],
    ):
        return
    draft = wait_until(
        lambda: (
            (d := author.get(f"{CONTROL}/drafts/{draft_id}").json()).get("state")
            in ("ready", "failed")
            and d
        ),
        timeout=120,
        interval=2,
        label="drafting",
    )
    if not step(
        results,
        "draft ready",
        bool(draft) and draft.get("state") == "ready",
        (draft or {}).get("detail", "")[:120],
    ):
        return
    verification = draft.get("verification") or {}
    say(f"verification: {json.dumps(verification)[:200]}")
    submitted = author.post(f"{CONTROL}/drafts/{draft_id}/submit")
    if not step(
        results,
        "draft submitted as the next contract version",
        submitted.status_code == 201,
        submitted.text[:160] if submitted.status_code != 201 else "",
    ):
        return
    version = submitted.json()
    if not promote_version(results, author, version["contract_id"], version["version"]):
        return

    say(
        "replaying the 8 stored T3 events as revision 2 "
        "(tools/mock_replay.py; the REST replay needs B4's lineage routes)"
    )
    consumer = tail_consumer(("norm.",))
    try:
        time.sleep(2)  # let the normalizer pick up the promoted version from `control`
        run_tool(
            [
                "tools/mock_replay.py",
                "--source",
                AUTHSRV,
                "--contains",
                "FAILED",
                "--limit",
                "8",
                "--revision",
                "2",
            ]
        )
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


def beat_evidence(results: Results) -> None:
    headline(
        5,
        "Evidence: verify, tamper, detect",
        "stored bytes are provably untouched, and an insider's edit is caught and located",
    )
    uids = STATE.get("sshd_uids") or []
    if not uids:
        consumer = tail_consumer(("raw.",))
        try:
            run_tool(["demo/tools/send_syslog.py", "--file", "linux_sshd.log"], show=False)
            found = collect(
                consumer, lambda t, p: p.get("source_id") == "src_lnx_core_07", timeout=20, want=1
            )
        finally:
            consumer.close()
        uids = [p["event_uid"] for _, p in found]
    if not step(results, "picked an archived sshd event", bool(uids)):
        return
    uid = uids[0]
    say(f"event {uid}")
    window = settings.merkle_window_seconds
    report = wait_until(
        lambda: (r := verify(uid)).get("verified") and r,
        timeout=settings.segment_max_seconds + 2 * window + 30,
        interval=3,
        label="waiting for the segment seal and the signed window root",
    )
    if not step(results, "verify passes (immudb step not implemented yet)", bool(report)):
        show_report(verify(uid))
        return
    show_report(report)

    say(bold("an insider with root and the encryption key rewrites the stored bytes ..."))
    run_tool(["tools/tamper.py", "insider_rewrite", "--event", uid])
    broken = verify(uid)
    failed = [s["id"] for s in broken.get("steps", []) if not s["ok"] and not s.get("status")]
    step(
        results,
        "verify turns red and locates the break",
        not broken.get("verified") and bool(failed),
        ", ".join(failed),
    )
    run_tool(["tools/tamper.py", "untamper", "--event", uid], show=False)
    step(results, "after untamper, verify is green again", bool(verify(uid).get("verified")))
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
        "--draft-mode",
        default="heuristic",
        choices=["heuristic", "cache", "live", "live_then_cache"],
    )
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
        reset = http(timeout=120).post(f"{CONTROL}/internal/reset", json={"scenario": "sih_main"})
        print(dim(f"  control plane reset to the seeded demo world (HTTP {reset.status_code})"))

    print(
        bold("\nVeyra guided demo")
        + dim(f"  (profile {settings.profile}, drafts: {args.draft_mode})")
    )
    print(
        dim(
            "  Open the console at http://localhost:8080 and the Kafka UI at "
            "http://localhost:8085 to watch along."
        )
    )
    results = Results()
    for n in beats:
        try:
            fn = BEATS[n]
            fn(results, args.draft_mode) if n in (3, 4) else fn(results)
        except Exception as exc:
            results.report("FAIL", f"beat {n} crashed: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=2)
        pause(args.auto)

    print(bold("\nWhat you just saw vs. what is not built yet"))
    say(
        "shown live: syslog + HTTP ingest, tiers 1-4, byte offsets, "
        "onboarding with four-eyes, drift, drafting,"
    )
    say(
        "            promotion, replay as revision 2, sealed evidence with verify, "
        "tamper detection, ledger audit"
    )
    say(
        yellow(
            "not built:  the router (so nothing reaches Wazuh through Veyra yet), the demo engine,"
        )
    )
    say(yellow("            the console's Lineage/Evidence pages, and the immudb step of verify"))
    return results.summary()


if __name__ == "__main__":
    raise SystemExit(main())
