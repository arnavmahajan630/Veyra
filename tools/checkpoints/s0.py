"""S0 acceptance checks (AC1-AC7 in docs/plan/shared/S0_bootstrap.md).

Same style as the CP1-CP4 scripts S1 defines: it only *observes* — it sends input through
the public paths and checks the outputs — and prints PASS/WARN/FAIL per criterion.

    make up PROFILE=laptop
    uv run python tools/checkpoints/s0.py            # all checks
    uv run python tools/checkpoints/s0.py --only ac3 --seconds 60 --eps 50

AC4 has a human half (the Wazuh dashboard in a browser); the script checks the
machine-verifiable half, that the NDJSON line reached the indexer.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
Row = tuple[str, str, str]

CORE_CONTAINERS = [
    "veyra-kafka",
    "veyra-clickhouse",
    "veyra-immudb",
    "veyra-caddy",
    "veyra-edge-dmz",
    "veyra-edge-core",
]
WAZUH_CONTAINERS = ["veyra-wazuh-indexer", "veyra-wazuh-manager", "veyra-wazuh-dashboard"]


def sh(*cmd: str, timeout: int = 60) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


# ---------------------------------------------------------------- AC1
def ac1_stack_healthy() -> list[Row]:
    rows: list[Row] = []
    _, out = sh("docker", "ps", "--format", "{{.Names}}\t{{.Status}}")
    status = dict(line.split("\t", 1) for line in out.splitlines() if "\t" in line)
    for name in CORE_CONTAINERS + WAZUH_CONTAINERS:
        state = status.get(name)
        if state is None:
            rows.append((FAIL if name in CORE_CONTAINERS else WARN, name, "not running"))
        elif "unhealthy" in state:
            rows.append((FAIL, name, state))
        else:
            rows.append((PASS, name, state))

    # Total memory across the stack; the laptop budget is <= 9 GB idle without Ollama.
    _, stats = sh("docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}")
    total_mb = 0.0
    for line in stats.splitlines():
        if "\t" not in line or not line.startswith("veyra-"):
            continue
        used = line.split("\t", 1)[1].split("/")[0].strip()
        match = re.match(r"([0-9.]+)\s*([GMK]i?B)", used)
        if not match:
            continue
        value, unit = float(match.group(1)), match.group(2)
        total_mb += value * {"GiB": 1024, "GB": 1024, "MiB": 1, "MB": 1}.get(unit, 1 / 1024)
    rows.append(
        (
            PASS if total_mb <= 9216 else WARN,
            "stack memory",
            f"{total_mb / 1024:.2f} GiB across veyra-* containers (budget 9 GiB idle)",
        )
    )
    return rows


# ---------------------------------------------------------------- AC2
def ac2_topics() -> list[Row]:
    from confluent_kafka.admin import AdminClient

    from veyra_common.settings import Settings
    from veyra_common.topics import topic_specs

    cfg = Settings(kafka_bootstrap=os.environ.get("VEYRA_KAFKA_BOOTSTRAP", "localhost:29092"))
    admin = AdminClient({"bootstrap.servers": cfg.kafka_bootstrap})
    try:
        cluster = admin.list_topics(timeout=20)
    except Exception as exc:
        return [(FAIL, "kafka", f"cannot list topics: {exc}")]

    rows: list[Row] = []
    describe_lines: list[str] = []
    for spec in topic_specs(cfg):
        meta = cluster.topics.get(spec.name)
        if meta is None:
            rows.append((FAIL, spec.name, "missing (run `make topics`)"))
            continue
        parts = len(meta.partitions)
        ok = parts == spec.partitions
        rows.append(
            (
                PASS if ok else FAIL,
                spec.name,
                f"{parts} partitions (want {spec.partitions})"
                + (", compacted" if spec.compacted else ""),
            )
        )
        describe_lines.append(f"{spec.name}\tpartitions={parts}\tcompact={spec.compacted}")

    out = REPO / "docs" / "plan" / "reports" / "S0-topics-describe.txt"
    out.write_text("\n".join(describe_lines) + "\n")
    rows.append((PASS, "describe output", f"saved to {out.relative_to(REPO)}"))
    return rows


# ---------------------------------------------------------------- AC3
def ac3_envelopes(eps: float, seconds: float) -> list[Row]:
    """fake_raw at `eps` for `seconds`, then count and validate what landed on raw.*.

    The pass condition is **no loss**: everything the producer reports sending must come
    back out of Kafka, validate against the IF-ENVELOPE model, and re-hash to the same
    raw_sha256. A host too slow to hit the requested rate is a WARN, not a failure.
    """
    from veyra_common.hashing import sha256_hex
    from veyra_common.kafka import make_consumer
    from veyra_common.models import Envelope
    from veyra_common.settings import Settings
    from veyra_common.topics import RAW_PATTERN

    bootstrap = os.environ.get("VEYRA_KAFKA_BOOTSTRAP", "localhost:29092")
    cfg = Settings(kafka_bootstrap=bootstrap)
    group = f"s0-ac3-{int(time.time())}"
    consumer = make_consumer(group, pattern=RAW_PATTERN, cfg=cfg, auto_offset_reset="latest")
    # Let the subscription settle before producing, so nothing is missed.
    deadline = time.monotonic() + 20
    while not consumer.assignment() and time.monotonic() < deadline:
        consumer.poll(0.5)

    env = os.environ | {"VEYRA_KAFKA_BOOTSTRAP": bootstrap}
    expected = int(eps * seconds)
    producer = subprocess.Popen(
        [
            sys.executable,
            str(REPO / "demo" / "tools" / "fake_raw.py"),
            "--eps",
            str(eps),
            "--seconds",
            str(seconds),
        ],
        cwd=REPO,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    valid = invalid = hash_bad = 0

    def drain(until: float) -> None:
        nonlocal valid, invalid, hash_bad
        while time.monotonic() < until:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            try:
                envelope = Envelope.model_validate_json(msg.value())
            except Exception:  # a bad record is counted, never raised
                invalid += 1
                continue
            valid += 1
            if sha256_hex(base64.b64decode(envelope.raw_b64)) != envelope.raw_sha256:
                hash_bad += 1

    drain(time.monotonic() + seconds)
    out, _ = producer.communicate(timeout=120)
    produced = 0
    for line in (out or "").splitlines():
        if line.startswith("sent ") and "envelopes" in line:
            produced = int(line.split()[1])
    # Give the tail of the stream time to arrive after the producer's final flush.
    drain(time.monotonic() + 20)
    consumer.close()

    return [
        (
            PASS if produced and valid >= produced else FAIL,
            "no events lost",
            f"{valid} consumed of {produced} produced",
        ),
        (
            PASS if produced >= expected * 0.98 else WARN,
            "rate achieved",
            f"{produced} in {seconds:.0f}s = {produced / max(seconds, 1):.1f} eps (target {eps})",
        ),
        (PASS if invalid == 0 else FAIL, "model validation", f"{invalid} invalid records"),
        (PASS if hash_bad == 0 else FAIL, "raw_sha256 recomputed", f"{hash_bad} mismatches"),
    ]


# ---------------------------------------------------------------- AC4
def ac4_wazuh() -> list[Row]:
    """Append one NDJSON line to the sink and look for it in the indexer."""
    rows: list[Row] = []
    sink = REPO / "data" / "sinks" / "wazuh" / "veyra.ndjson"
    marker = f"s0-ac4-{int(time.time())}"
    line = {
        "class_uid": 0,
        "category_uid": 0,
        "type_uid": 99,
        "activity_id": 99,
        "severity_id": 3,
        "time": int(time.time() * 1000),
        # The marker IS the whole message: data.message is a keyword field in the
        # wazuh-alerts template, so a partial phrase query never matches it.
        "message": marker,
        "raw_data": f"S0 AC4 probe {marker}",
        "veyra": {
            "tier": 4,
            "class": 0,
            "tenant": "t_ntro_core",
            "source": "s0-acceptance",
            "revision": 1,
        },
    }
    try:
        with sink.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line) + "\n")
        rows.append((PASS, "ndjson sink", f"appended probe to {sink.relative_to(REPO)}"))
    except OSError as exc:
        return [(FAIL, "ndjson sink", f"cannot write {sink}: {exc}")]

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    password = os.environ.get("VEYRA_WAZUH_INDEXER_PASSWORD", "admin")
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    # Match our rule and our marker: the manager polls the file, analysisd writes
    # alerts.json, then filebeat ships it, so this can take well over a minute on a
    # laptop under load.
    query = json.dumps(
        {
            "query": {
                "bool": {
                    "filter": [
                        # match_phrase, not term: rule.id is mapped as analyzed text in
                        # the wazuh-alerts template, so a term query never matches it.
                        {"match_phrase": {"rule.id": "100100"}},
                        {"term": {"data.message": marker}},
                    ]
                }
            },
            "size": 1,
        }
    ).encode()

    found = False
    deadline = time.monotonic() + 180  # logcollector poll + analysisd + filebeat ship
    last_error = ""
    while time.monotonic() < deadline and not found:
        request = urllib.request.Request(
            "https://localhost:9200/wazuh-alerts-*/_search",
            data=query,
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10, context=ctx) as resp:
                body = json.loads(resp.read())
            found = body.get("hits", {}).get("total", {}).get("value", 0) > 0
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        if not found:
            time.sleep(5)

    rows.append(
        (
            PASS if found else WARN,
            "indexer alert",
            "probe visible in wazuh-alerts-*"
            if found
            else f"not found within 180s ({last_error or 'no hits'}) — check rule 100100",
        )
    )
    rows.append((WARN, "dashboard (human)", "open https://localhost:8443 and confirm in Discover"))
    return rows


# ---------------------------------------------------------------- AC5
def ac5_ollama_from_container() -> list[Row]:
    """The JSON-schema request must work from *inside* a container via host.docker.internal."""
    model = os.environ.get("VEYRA_LLM_MODEL", "qwen2.5:3b")
    payload = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0},
        "format": {
            "type": "object",
            "properties": {
                "class": {"type": "string"},
                "confidence": {"type": "string"},
            },
            "required": ["class", "confidence"],
        },
        "messages": [
            {
                "role": "user",
                "content": "Return JSON with class=authentication and confidence=high.",
            }
        ],
    }
    code, out = sh(
        "docker",
        "run",
        "--rm",
        "--add-host",
        "host.docker.internal:host-gateway",
        "veyra/python:0.1.0",
        "python",
        "-c",
        "import json,sys,urllib.request;"
        f"req=urllib.request.Request('http://host.docker.internal:11434/api/chat',"
        f"data=json.dumps({payload!r}).encode(),headers={{'Content-Type':'application/json'}});"
        "r=json.loads(urllib.request.urlopen(req,timeout=120).read());"
        "c=json.loads(r['message']['content']);print(json.dumps(c))",
        timeout=200,
    )
    if code != 0:
        return [(FAIL, "ollama in container", out.splitlines()[-1] if out else "failed")]
    try:
        parsed = json.loads(out.splitlines()[-1])
    except json.JSONDecodeError:
        return [(FAIL, "ollama in container", f"non-JSON answer: {out[-200:]}")]
    return [
        (PASS, "ollama in container", f"schema-valid JSON via host.docker.internal: {parsed}"),
    ]


# ---------------------------------------------------------------- AC6 / AC7
def ac6_tests_and_lint() -> list[Row]:
    rows: list[Row] = []
    code, out = sh("uv", "run", "pytest", "-q", "-m", "not int", timeout=600)
    rows.append((PASS if code == 0 else FAIL, "make test", out.splitlines()[-1] if out else ""))
    code, out = sh("uv", "run", "ruff", "check", ".", timeout=300)
    rows.append((PASS if code == 0 else FAIL, "ruff check", out.splitlines()[-1] if out else ""))
    code, out = sh("uv", "run", "ruff", "format", "--check", ".", timeout=300)
    rows.append((PASS if code == 0 else FAIL, "ruff format", out.splitlines()[-1] if out else ""))
    return rows


def ac7_plan_check() -> list[Row]:
    code, out = sh("uv", "run", "python", str(REPO / "tools" / "plan_check.py"), timeout=120)
    return [(PASS if code == 0 else FAIL, "plan-check", out.splitlines()[-1] if out else "")]


CHECKS = {
    "ac1": ("AC1 stack healthy and inside the memory budget", ac1_stack_healthy),
    "ac2": ("AC2 every IF-TOPICS topic with profile partitions", ac2_topics),
    "ac3": ("AC3 valid envelopes end to end", None),  # needs args
    "ac4": ("AC4 Wazuh sees a VEYRA NDJSON line", ac4_wazuh),
    "ac5": ("AC5 Ollama JSON schema from inside a container", ac5_ollama_from_container),
    "ac6": ("AC6 tests, lint and the frozen vectors", ac6_tests_and_lint),
    "ac7": ("AC7 no stale plan files", ac7_plan_check),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", choices=sorted(CHECKS), dest="only")
    parser.add_argument("--eps", type=float, default=50.0, help="AC3 events per second")
    parser.add_argument("--seconds", type=float, default=60.0, help="AC3 duration")
    args = parser.parse_args()

    selected = args.only or sorted(CHECKS)
    failures = 0
    for key in selected:
        title, fn = CHECKS[key]
        print(f"\n=== {title} ===")
        rows = ac3_envelopes(args.eps, args.seconds) if key == "ac3" else fn()  # type: ignore[misc]
        width = max(len(check) for _, check, _ in rows)
        for status, check, detail in rows:
            print(f"{status:<5} {check:<{width}}  {detail}")
        failures += sum(1 for status, _, _ in rows if status == FAIL)

    print(f"\nS0 acceptance: {'PASS' if failures == 0 else f'{failures} FAILED checks'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
