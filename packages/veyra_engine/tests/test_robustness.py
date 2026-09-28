"""A4's robustness rules: RE2 only, never raise, always bounded, always accounted for.

The fuzz test is the centrepiece. It is not looking for a specific bug — it is asserting the four
properties that make the hot path safe to point at the internet:

* ``normalize`` never raises, whatever bytes arrive;
* the tier is always 1-4, so a record can always be routed (P2);
* every event carries its raw bytes, so evidence is never lost (P1);
* every claim is either located or declared computed, so nothing is unexplained (P4).
"""

from __future__ import annotations

import ast
import random
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope, NormEvent
from veyra_engine import Engine, EngineContext, provenance_check

ENGINE_SRC = Path(__file__).resolve().parents[1] / "src" / "veyra_engine"
CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# `re` is allowed nowhere in the engine. `template_sig` needs it, which is exactly why that function
# lives in veyra_common.hashing instead of here — a deliberate placement this test protects.
RE_ALLOWLIST: frozenset[str] = frozenset()


def engine_modules() -> list[Path]:
    return sorted(path for path in ENGINE_SRC.rglob("*.py") if path.name != "__pycache__")


def test_the_engine_imports_re2_and_never_re() -> None:
    """Linear-time matching only: a backtracking regex is a denial of service on hostile input."""
    offenders: list[str] = []
    for module in engine_modules():
        if module.stem in RE_ALLOWLIST:
            continue
        tree = ast.parse(module.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "re" or alias.name.startswith("re."):
                        offenders.append(f"{module.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module == "re":
                names = ", ".join(alias.name for alias in node.names)
                offenders.append(f"{module.name}: from re import {names}")
    assert not offenders, (
        "veyra_engine must use RE2 only (A4). Offenders: "
        + "; ".join(offenders)
        + ". If a stdlib regex is genuinely needed, put it in veyra_common, as template_sig is."
    )


def test_at_least_one_engine_module_actually_uses_re2() -> None:
    """Guards against the previous test passing because nothing matches at all."""
    users = [m.name for m in engine_modules() if "import re2" in m.read_text()]
    assert users, "no engine module imports re2 — is the pattern code still there?"


def envelope(raw: bytes) -> Envelope:
    return stamp(
        raw,
        collector_id="fuzz",
        transport="syslog_udp",
        framing_method="datagram",
        source_id="src_nobody",
        tenant_id="t_maha_power",
        vendor="unregistered",
        zone="dmz",
        event_uid="0192a4f0-0000-7000-8000-000000000001",
        received_time=RECEIVED,
    )


ENGINE = Engine(EngineContext())


def check_invariants(raw: bytes) -> None:
    """The four properties, asserted for one input."""
    result = ENGINE.normalize(envelope(raw))

    assert 1 <= result.tier <= 4, f"tier {result.tier} for {raw[:40]!r}"
    assert result.ulpf["tier"] == result.tier
    assert result.ocsf["raw_data"] is not None, "the raw text must always survive (P1)"
    if result.tier >= 2:
        assert result.dlq is not None, "a degraded event must carry a DLQ copy (P2)"
        assert result.dlq.reason_code
    # Every claim accounted for, and every offset honest (P4).
    failures = [c for c in provenance_check(result.ocsf, envelope(raw).raw_bytes) if not c.ok]
    assert not failures, [(c.ocsf_path, c.reason[:80]) for c in failures]
    # The event must still be a valid IF-NORM-EVENT, whatever went in.
    NormEvent.model_validate(result.ocsf)
    # Bounded, but deliberately a loose ceiling. A per-example timing assertion is a flake magnet
    # on a laptop that is also running the stack — this caught a false failure during a concurrent
    # docker build. The 2x-budget claim AC4 actually makes is measured on its own, unloaded, in
    # `test_tier3.test_ac4_pathological_line_is_bounded`; what matters here is only that no input
    # sends the engine somewhere quadratic.
    assert result.timings_us["total"] <= 200_000, result.timings_us


@given(st.binary(min_size=0, max_size=4096))
@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_random_bytes(raw: bytes) -> None:
    check_invariants(raw)


@given(st.text(min_size=0, max_size=2048))
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_random_text(text: str) -> None:
    check_invariants(text.encode("utf-8", errors="replace"))


# Fragments real logs are made of, so the fuzzer spends its time on plausible shapes rather than
# random noise that all falls out as tier 4.
FRAGMENTS = [
    "<134>",
    "<86>",
    "Sep 26 14:05:11",
    "2026-09-26T14:05:11Z",
    "fw01",
    "core-lnx-07",
    "app[233]:",
    "sshd[4410]:",
    '{"evt":"auth"}',
    '{"msg":"user=a.sharma FAILED"}',
    "user=a.sharma",
    "password=hunter2",
    "from 103.21.4.77",
    "port 52144",
    "CEF:0|Acme|NGFW|9.1|100|deny|5|",
    "LEEF:1.0|Acme|NGFW|9.1|100|",
    "src=45.12.3.9",
    "dst=10.2.3.4",
    "attempts:1",
    "टर्बाइन-1 दबाव",
    ";HIST01  ;TAG0001 ;",
    "| trace=",
    "  at com.x.Auth.login(Auth.java:88)",
    "\\x00",
    '"unterminated',
    "}}}}",
    "=" * 20,
    "a" * 200,
]


@given(st.lists(st.sampled_from(FRAGMENTS), min_size=1, max_size=12))
@settings(max_examples=600, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_plausible_log_shapes(parts: list[str]) -> None:
    check_invariants(" ".join(parts).encode("utf-8", errors="replace"))


@given(
    st.integers(min_value=1, max_value=60),
    st.sampled_from(['{"a":', "[", '{"k":"v"', "]"]),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fuzz_deep_and_broken_json(depth: int, fragment: str) -> None:
    check_invariants((fragment * depth).encode())


def test_fuzz_corpus_mutations() -> None:
    """Bit-flips and truncations of the real corpus.

    These are the mutations most likely to break a parser: a half-written line, a flipped byte in
    the middle of a UTF-8 sequence, a line pasted onto itself.
    """
    random.seed(20260928)
    lines: list[bytes] = []
    names = ("linux_sshd.log", "acme_ngfw_cef.log", "authsrv_t3_failed.log", "ot_historian.log")
    for name in names:
        lines.extend(framed.raw for framed in split_lines((CORPUS / name).read_bytes()))
    lines.append((CORPUS / "garbage.bin").read_bytes()[:8192])
    assert lines

    for line in lines:
        for _ in range(12):
            mutated = bytearray(line)
            if not mutated:
                continue
            style = random.choice(("flip", "truncate", "insert", "duplicate"))
            if style == "flip":
                index = random.randrange(len(mutated))
                mutated[index] = random.randrange(256)
            elif style == "truncate":
                mutated = mutated[: random.randrange(len(mutated) + 1)]
            elif style == "insert":
                index = random.randrange(len(mutated) + 1)
                mutated[index:index] = bytes([random.randrange(256)])
            else:
                mutated = mutated * 2
            check_invariants(bytes(mutated))


@pytest.mark.slow
def test_fuzz_fifty_thousand_cases() -> None:
    """AC3: >= 50k cases, zero exceptions. Run deliberately — it takes a few minutes.

    uv run pytest packages/veyra_engine/tests/test_robustness.py -m slow
    """
    random.seed(4242)
    alphabet = bytes(range(256))
    seen = 0
    for _ in range(50_000):
        length = random.choice((0, 1, 2, 8, 32, 120, 512, 2048))
        if random.random() < 0.5:
            raw = bytes(random.choice(alphabet) for _ in range(length))
        else:
            raw = " ".join(random.choice(FRAGMENTS) for _ in range(random.randint(1, 8))).encode(
                "utf-8", errors="replace"
            )
        result = ENGINE.normalize(envelope(raw))
        assert 1 <= result.tier <= 4
        assert result.ocsf["raw_data"] is not None
        seen += 1
    assert seen == 50_000
