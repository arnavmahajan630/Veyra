"""Tier 3: the phase's acceptance criteria, as tests.

Tier 3 is what a judge sees in demo Beat 3 — a log nobody wrote a contract for, arriving in Wazuh
with its IPs and users extracted and every one of them traceable to real bytes. So the assertions
here are about exactly that, and about the three things tier 3 must refuse to do: set ``class_uid``,
assign an IP to an endpoint, or claim a byte range it cannot prove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.models import Envelope, NormEvent
from veyra_engine import Engine, EngineContext, mini_compile, provenance_check
from veyra_engine.class_hint import class_hint, severity_from_words
from veyra_engine.decode import decode
from veyra_engine.tier3 import cascade
from veyra_engine.tokens import extract_tokens

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "demo" / "corpus"
TEST_CONTRACTS = Path(__file__).resolve().parent / "contracts"
RECEIVED = "2026-09-26T14:10:00.000000000Z"

# The demo's T3 event: syslog + JSON + kv inside the message + a stack-trace continuation.
T3 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)
HINDI_LINE = (
    "26-09-2026 14:05:04;HIST01  ;TAG0005 ;जनरेटर लोड;OK    ;   812.500;MW   ;"
    "SEQ00004;OPR=maint01;SITE=MAHA-PUNE-1"
).encode()


def envelope(raw: bytes, *, source: str = "src_nobody", vendor: str = "unregistered") -> Envelope:
    return stamp(
        raw,
        collector_id="t3",
        transport="syslog_udp",
        framing_method="datagram",
        source_id=source,
        tenant_id="t_maha_power",
        vendor=vendor,
        zone="dmz",
        event_uid="0192a4f0-0000-7000-8000-000000000001",
        received_time=RECEIVED,
    )


def bare_engine() -> Engine:
    """No contracts loaded at all — every event takes the tier-3 path."""
    return Engine(EngineContext())


def engine_with_authsrv_v1() -> Engine:
    """authsrv@1 covers T1/T2 but not the FAILED shape, which is the demo's premise."""
    engine = Engine(EngineContext())
    engine.load([mini_compile((TEST_CONTRACTS / "authsrv.yaml").read_text())])
    return engine


# ---------------------------------------------------------------- AC1
def test_ac1_the_demo_t3_event_is_tier_three_and_fully_extracted() -> None:
    engine = engine_with_authsrv_v1()
    env = envelope(T3, source="src_authsrv_01", vendor="custom")
    result = engine.normalize(env)

    assert result.tier == 3
    assert result.conformance == "unknown_template"
    # A contract exists but no template matched, so that is the reason the drift worker clusters on.
    assert result.dlq is not None
    assert result.dlq.reason_code == "no_template_match"

    values = {obs["value"] for obs in result.ocsf["observables"]}
    assert "103.21.4.77" in values, "the attacker IP must be extracted"
    assert "10.2.3.4" in values
    assert "a.sharma" in values, "the user must be extracted"

    unmapped = result.ocsf["unmapped"]
    assert unmapped.get("attempts") == "1"
    assert any("trace" in str(key) or "trace" in str(value) for key, value in unmapped.items())

    assert result.ulpf["class_hint"] == {"class_uid": 3002, "confidence": "medium"}

    # 100 % of offsets must slice their own value out of the raw bytes.
    checks = provenance_check(result.ocsf, env.raw_bytes)
    assert checks, "tier 3 must locate something"
    assert all(check.ok for check in checks), [c for c in checks if not c.ok]


def test_tier_three_never_sets_a_class_or_an_endpoint() -> None:
    """The two guesses tier 3 is forbidden to make, because a SIEM rule would believe them."""
    engine = engine_with_authsrv_v1()
    result = engine.normalize(envelope(T3, source="src_authsrv_01", vendor="custom"))

    assert result.ocsf["class_uid"] == 0, "a hint is not a mapping"
    assert result.ocsf["category_uid"] == 0
    assert "src_endpoint" not in result.ocsf, "at tier 3 nothing says which end is which"
    assert "dst_endpoint" not in result.ocsf
    assert result.ulpf["class_hint"]["class_uid"] == 3002, "the guess lives here instead"


def test_tier_three_events_satisfy_if_norm_event() -> None:
    engine = bare_engine()
    for raw in (T3, HINDI_LINE, b"totally plain text with no structure"):
        NormEvent.model_validate(engine.normalize(envelope(raw)).ocsf)


# ---------------------------------------------------------------- AC2
def test_ac2_devanagari_offsets_are_byte_correct() -> None:
    """The OT historian is the reason offsets are bytes and not characters."""
    engine = bare_engine()
    env = envelope(HINDI_LINE)
    result = engine.normalize(env)

    assert result.tier == 3
    checks = provenance_check(result.ocsf, env.raw_bytes)
    assert checks
    assert all(check.ok for check in checks), [c for c in checks if not c.ok]

    # And prove it the blunt way: every offset, sliced straight out of the raw bytes.
    for path, (start, end) in result.ulpf["field_offsets"].items():
        sliced = env.raw_bytes[start:end].decode("utf-8")
        assert sliced, path
    assert "OPR" in result.ocsf["unmapped"] or "maint01" in str(result.ocsf["unmapped"])


def test_multibyte_text_does_not_shift_later_offsets() -> None:
    """A Devanagari field early in the line must not corrupt offsets after it."""
    raw = "tag=दबाव user=r.patil ip=10.4.1.20".encode()
    engine = bare_engine()
    env = envelope(raw)
    result = engine.normalize(env)
    located = {
        path: env.raw_bytes[span[0] : span[1]].decode()
        for path, span in result.ulpf["field_offsets"].items()
    }
    assert "r.patil" in located.values() or "r.patil" in str(result.ocsf["observables"])
    assert all(check.ok for check in provenance_check(result.ocsf, env.raw_bytes))


# ---------------------------------------------------------------- AC4 (budget / size)
def test_ac4_pathological_line_is_bounded() -> None:
    """AC4: a 60 KB `a=` line finishes within 2x the budget, as tier 3 or tier 4 budget_exceeded.

    The median of several runs is what is asserted: this is wall-clock on a laptop that also runs
    Kafka and an OpenSearch JVM, so a single run's tail says more about the machine than the engine.
    """
    raw = b"a=" * 30_000
    engine = bare_engine()
    engine.normalize(envelope(raw))  # warm the validator cache

    timings = sorted(engine.normalize(envelope(raw)).timings_us["total"] for _ in range(7))
    result = engine.normalize(envelope(raw))

    assert result.tier in (3, 4)
    if result.tier == 4:
        assert result.dlq is not None
        assert result.dlq.reason_code in ("budget_exceeded", "size_exceeded")
    # Whatever the tier, the event still carries its bytes (P2).
    assert result.ocsf["raw_data"]
    assert result.ocsf["raw_data"].encode().startswith(b"a=a=")

    median = timings[len(timings) // 2]
    assert median <= engine.ctx.budget_us * 2, (
        f"median {median} us exceeds 2x the {engine.ctx.budget_us} us budget: {timings}"
    )


def test_size_cap_parses_a_prefix_but_keeps_the_whole_raw() -> None:
    engine = Engine(EngineContext(max_event_bytes=256))
    raw = b"<134>Sep 26 14:05:11 fw01 app[233]: start " + b"x" * 5_000 + b" end"
    result = engine.normalize(envelope(raw))
    assert result.ocsf["raw_data"].encode().endswith(b" end"), "raw_data must be complete (P1)"
    assert result.tier in (3, 4)


def test_budget_is_not_charged_for_one_time_warm_up() -> None:
    """Compiling validators and loading a timezone happen at load(), not on the first event.

    This is a regression test: the budget guard originally fired on a perfectly good tier-1 event
    because a cold process spent ~14 ms compiling the OCSF validator, three times the budget.
    """
    engine = engine_with_authsrv_v1()
    raw = (
        b'<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK login '
        b'from 10.4.1.20 via 10.2.3.4"}'
    )
    first = engine.normalize(envelope(raw, source="src_authsrv_01", vendor="custom"))
    assert first.tier == 1, f"the first event must not be downgraded: {first.dlq}"
    assert first.timings_us["total"] < engine.ctx.budget_us


# ---------------------------------------------------------------- cascade
def test_cascade_detects_syslog_then_json_then_the_remainder() -> None:
    parse_path, fields, region = cascade(decode(T3))
    assert parse_path[0] == "auto:rfc3164"
    assert "auto:json" in parse_path
    paths = {f.path for f in fields}
    assert "json.evt" in paths
    assert "json.__rest" in paths, "the trailing `| trace=` must be captured, not dropped"
    assert "user=a.sharma" in region.text, "the text field should be the JSON message"


def test_cascade_detects_cef() -> None:
    raw = b"CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny"
    parse_path, fields, _ = cascade(decode(raw))
    assert "auto:cef" in parse_path
    assert {f.path for f in fields} >= {"cef.device_vendor", "cef.src", "cef.dst"}


def test_cascade_detects_a_delimited_line() -> None:
    parse_path, fields, _ = cascade(decode(HINDI_LINE))
    assert any("csv" in step for step in parse_path), parse_path
    assert len(fields) >= 4


def test_cascade_falls_back_to_plain_text() -> None:
    parse_path, fields, region = cascade(decode(b"nothing structured about this at all"))
    assert parse_path == ["auto:text"]
    assert fields == []
    assert region.text.startswith("nothing")


def test_cascade_is_recorded_in_the_parse_path() -> None:
    engine = bare_engine()
    result = engine.normalize(envelope(T3))
    assert result.ulpf["parse_path"][0] == "no_contract"
    assert "auto:rfc3164" in result.ulpf["parse_path"]


# ---------------------------------------------------------------- class hints and severity
@pytest.mark.parametrize(
    ("text", "expected_class", "expected_confidence"),
    [
        ("user=bob FAILED login from 1.2.3.4", 3002, "medium"),
        ("sshd authentication failure", 3002, "low"),
        ("traffic deny src=1.2.3.4 dst=5.6.7.8 port 22", 4001, "medium"),
        ("GET /index.html HTTP/1.1 200", 4002, "low"),
        ("exec /usr/bin/thing pid 4242", 1007, "low"),
    ],
)
def test_class_hints(text: str, expected_class: int, expected_confidence: str) -> None:
    hint = class_hint(text, extract_tokens(text))
    assert hint is not None, text
    assert hint.class_uid == expected_class
    assert hint.confidence == expected_confidence


def test_no_hint_when_nothing_matches() -> None:
    assert class_hint("the quick brown fox", extract_tokens("the quick brown fox")) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("login FAILED for user", 3),
        ("connection denied", 3),
        ("kernel panic", 4),
        ("CRITICAL disk error", 4),
        ("everything is fine", None),
    ],
)
def test_severity_words(text: str, expected: int | None) -> None:
    assert severity_from_words(text) == expected


def test_severity_is_recorded_as_derived_not_located() -> None:
    engine = bare_engine()
    result = engine.normalize(envelope(T3))
    assert result.ocsf["severity_id"] == 3
    assert result.ulpf["derived_fields"]["severity_id"] == "vocab:severity_words"
    assert "severity_id" not in result.ulpf["field_offsets"], "a vocabulary hit is not in the bytes"


# ---------------------------------------------------------------- time
def test_time_comes_from_a_timestamp_token_when_one_parses() -> None:
    raw = b"2026-09-26T14:05:11Z something happened to user=bob"
    result = bare_engine().normalize(envelope(raw))
    assert result.ulpf["time"]["source"] == "event"
    assert result.ulpf["derived_fields"]["time"] == "ts:token"


def test_time_falls_back_to_arrival_with_no_timestamp() -> None:
    result = bare_engine().normalize(envelope(b"user=bob did a thing"))
    assert result.ulpf["time"]["source"] == "received"
    assert result.ulpf["derived_fields"]["time"] == "received"


# ---------------------------------------------------------------- determinism
def test_tier_three_is_deterministic() -> None:
    engine = bare_engine()
    env = envelope(T3)
    first = engine.normalize(env)
    for _ in range(20):
        again = engine.normalize(env)
        assert again.ocsf["observables"] == first.ocsf["observables"]
        assert again.ulpf["field_offsets"] == first.ulpf["field_offsets"]
        assert again.ulpf["parse_path"] == first.ulpf["parse_path"]


def test_observable_names_follow_the_convention() -> None:
    raw = b"conn from 1.2.3.4 to 5.6.7.8 by user=bob on host app.example.com"
    result = bare_engine().normalize(envelope(raw))
    names = [obs["name"] for obs in result.ocsf["observables"]]
    assert "ip_1" in names and "ip_2" in names
    assert "user" in names
    assert any(name.startswith("host") for name in names)


# ---------------------------------------------------------------- corpus
def test_every_ot_historian_line_reaches_tier_three_with_offsets() -> None:
    engine = bare_engine()
    lines = [framed.raw for framed in split_lines((CORPUS / "ot_historian.log").read_bytes())]
    assert lines
    for index, raw in enumerate(lines):
        env = envelope(raw)
        result = engine.normalize(env)
        assert result.tier == 3, f"line {index} is tier {result.tier}"
        assert result.ocsf["observables"] or result.ocsf["unmapped"], (
            f"line {index} extracted nothing"
        )
        assert all(c.ok for c in provenance_check(result.ocsf, env.raw_bytes)), f"line {index}"


def test_sshd_lines_the_seed_contract_misses_are_now_tier_three() -> None:
    """The 13 lines A3 emitted as tier 4 are exactly what tier 3 exists for."""
    engine = bare_engine()
    lines = [framed.raw for framed in split_lines((CORPUS / "linux_sshd.log").read_bytes())]
    tier3_count = 0
    for raw in lines:
        env = envelope(raw)
        result = engine.normalize(env)
        assert result.tier == 3
        tier3_count += 1
        assert all(c.ok for c in provenance_check(result.ocsf, env.raw_bytes))
    assert tier3_count == len(lines)
