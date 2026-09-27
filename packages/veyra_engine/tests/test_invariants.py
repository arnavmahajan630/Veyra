"""The invariants that make the hot path trustworthy: purity, determinism, never raising.

A3 task 8 asks for a determinism test across two processes. This file does that, and adds the
stronger version of the same claim: the engine is proven not to *call* the clock at all, by
making every clock function raise while a full corpus is normalized. If anything reached for
``time.time()`` or ``datetime.now()``, these tests fail loudly (P3).
"""

from __future__ import annotations

import datetime as datetime_module
import json
import subprocess
import sys
import time as time_module
from pathlib import Path

import pytest

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope
from veyra_engine import Engine, EngineContext, mini_compile, serialize

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
CONTRACT = REPO / "packages" / "veyra_engine" / "tests" / "contracts" / "authsrv.yaml"
SEED = REPO / "contracts-repo" / "t_ntro_core" / "linux_sshd.yaml"
RECEIVED = "2026-09-26T14:10:00.000000000Z"


def envelope(raw: bytes, index: int = 0, source: str = "src_authsrv_01") -> Envelope:
    return stamp(
        raw,
        collector_id="inv",
        transport="syslog_tcp",
        framing_method="newline",
        source_id=source,
        tenant_id="t_maha_power",
        vendor="custom",
        zone="dmz",
        event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
        received_time=RECEIVED,
    )


def engine_with(contract: Path) -> Engine:
    engine = Engine(EngineContext())
    engine.load([mini_compile(contract.read_text())])
    return engine


def corpus(name: str) -> list[bytes]:
    return [framed.raw for framed in split_lines((CORPUS / name).read_bytes())]


# ---------------------------------------------------------------- purity
def test_normalize_never_reads_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """P3, proven rather than asserted: every clock call raises during a full run."""

    def forbidden(*args: object, **kwargs: object) -> float:
        raise AssertionError("the engine read the clock — normalize must be pure")

    monkeypatch.setattr(time_module, "time", forbidden)
    monkeypatch.setattr(time_module, "time_ns", forbidden)

    class ForbiddenDatetime(datetime_module.datetime):
        @classmethod
        def now(cls, tz: object = None) -> ForbiddenDatetime:  # type: ignore[override]
            raise AssertionError("the engine called datetime.now — normalize must be pure")

        @classmethod
        def utcnow(cls) -> ForbiddenDatetime:  # type: ignore[override]
            raise AssertionError("the engine called datetime.utcnow — normalize must be pure")

    monkeypatch.setattr(datetime_module, "datetime", ForbiddenDatetime)

    engine = engine_with(CONTRACT)
    for index, raw in enumerate(corpus("authsrv_t1_ok.log")):
        result = engine.normalize(envelope(raw, index))
        assert result.tier == 1


def test_the_same_envelope_normalizes_identically_every_time() -> None:
    engine = engine_with(CONTRACT)
    env = envelope(corpus("authsrv_t1_ok.log")[0])
    first = serialize(engine.normalize(env).ocsf)
    for _ in range(50):
        assert serialize(engine.normalize(env).ocsf) == first


def test_two_engine_instances_agree() -> None:
    raw = corpus("authsrv_t1_ok.log")[0]
    left = serialize(engine_with(CONTRACT).normalize(envelope(raw)).ocsf)
    right = serialize(engine_with(CONTRACT).normalize(envelope(raw)).ocsf)
    assert left == right


def test_determinism_across_two_processes() -> None:
    """A3 task 8: byte-identical output from a separate interpreter."""
    script = """
import json, sys
from pathlib import Path
from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_engine import Engine, EngineContext, mini_compile, serialize

repo = Path(sys.argv[1])
engine = Engine(EngineContext())
contract = repo / "packages/veyra_engine/tests/contracts/authsrv.yaml"
engine.load([mini_compile(contract.read_text())])
out = []
raws = [f.raw for f in split_lines((repo / "demo/corpus/authsrv_t1_ok.log").read_bytes())]
for index, raw in enumerate(raws):
    env = stamp(raw, collector_id="inv", transport="syslog_tcp", framing_method="newline",
                source_id="src_authsrv_01", tenant_id="t_maha_power", vendor="custom", zone="dmz",
                event_uid=f"0192a4f0-0000-7000-8000-{index:012d}",
                received_time="2026-09-26T14:10:00.000000000Z")
    out.append(serialize(engine.normalize(env).ocsf).decode())
print(json.dumps(out))
"""
    runs = []
    for _ in range(2):
        proc = subprocess.run(
            [sys.executable, "-c", script, str(REPO)],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO,
        )
        runs.append(json.loads(proc.stdout))
    assert runs[0] == runs[1], "two processes produced different bytes"

    in_process = [
        serialize(engine_with(CONTRACT).normalize(envelope(raw, i)).ocsf).decode()
        for i, raw in enumerate(corpus("authsrv_t1_ok.log"))
    ]
    assert runs[0] == in_process, "a subprocess disagreed with this process"


def test_serialization_is_sorted_compact_and_has_integer_times() -> None:
    result = engine_with(CONTRACT).normalize(envelope(corpus("authsrv_t1_ok.log")[0]))
    payload = serialize(result.ocsf)
    assert b", " not in payload and b'": ' not in payload, "must be compact"
    decoded = json.loads(payload)
    assert list(decoded) == sorted(decoded), "keys must be sorted"
    assert isinstance(decoded["time"], int), "time must be an int, never a float"


# ---------------------------------------------------------------- robustness
@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b" ",
        b"\x00",
        b"\xff\xfe\xfd",
        b"<134>",
        b"<134>Sep 26 14:05:11 fw01 app[233]: ",
        b'<134>Sep 26 14:05:11 fw01 app[233]: {"unterminated":',
        b'<134>Sep 26 14:05:11 fw01 app[233]: {"msg":',
        b"<999999>Sep 26 14:05:11 h a: x",
        b"a" * 70_000,
        b"=" * 1000,
        b"k=" * 5000,
        '<134>Sep 26 14:05:11 fw01 app[233]: {"msg":"सिस्टम त्रुटि"}'.encode(),
        b"\n\n\n",
        b"CEF:0|",
        b"LEEF:1.0|",
        b"{}",
        b"[]",
        b"null",
    ],
)
def test_hostile_input_never_raises_and_always_produces_a_valid_tier(raw: bytes) -> None:
    engine = engine_with(CONTRACT)
    result = engine.normalize(envelope(raw))
    assert 1 <= result.tier <= 4
    assert result.ocsf["raw_data"] is not None
    assert result.ulpf["event_uid"] == result.ocsf["ulpf"]["event_uid"]
    if result.tier >= 2:
        assert result.dlq is not None, "a degraded event must carry a DLQ copy (P2)"


def test_a_broken_template_does_not_disable_the_rest_of_the_contract() -> None:
    """One bad pattern is a contract bug; the other templates must keep working."""
    engine = Engine(EngineContext())
    engine.load(
        [
            {
                "contract": "mixed",
                "version": 1,
                "sources": ["src_x"],
                "envelope": [],
                "templates": [
                    {"id": "broken", "regex": "([unclosed", "captures": []},
                    {
                        "id": "good",
                        "regex": r"^hello (?P<who>\w+)$",
                        "captures": [{"name": "who", "type": "token"}],
                        "class_uid": 0,
                        "activity_id": 99,
                        "category": "uncategorized",
                        "map": [{"ocsf_path": "message", "kind": "capture", "ref": "who"}],
                    },
                ],
            }
        ]
    )
    assert engine.load_errors, "the broken template must be reported"
    result = engine.normalize(envelope(b"hello world", source="src_x"))
    assert result.tier == 1
    assert result.ocsf["message"] == "world"


def test_contract_swap_is_atomic_under_repeated_loads() -> None:
    """`load()` must never leave a half-built set visible."""
    engine = engine_with(CONTRACT)
    raw = corpus("authsrv_t1_ok.log")[0]
    for _ in range(20):
        assert engine.normalize(envelope(raw)).tier == 1
        engine.load([mini_compile(CONTRACT.read_text())])
        assert engine.contracts_loaded == 1
    engine.load([])
    assert engine.normalize(envelope(raw)).tier == 4


def test_tier_and_conformance_always_agree() -> None:
    engine = engine_with(SEED)
    pairs = {1: "match", 2: "partial", 3: "unknown_template", 4: "unparseable"}
    for index, raw in enumerate(corpus("linux_sshd.log")):
        result = engine.normalize(envelope(raw, index, source="src_lnx_core_07"))
        assert pairs[result.tier] == result.conformance
        assert result.ulpf["tier"] == result.tier
        assert result.ulpf["conformance"] == result.conformance
