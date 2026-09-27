"""The IF-ENGINE-LIB surface and the engine's invariants.

Written in S0 against the stub and kept unchanged through A3 on purpose: these are the
promises Track C builds on (names, signatures) and the properties the hot path must hold
whatever the internals do — purity, every tier in 1..4, never raising, spans that slice back
to their value. The only edits A3 made were to the three `decode` tests, because `decode` now
returns a `Decoded` object with the char->byte map instead of a bare tuple.
"""

from __future__ import annotations

import inspect

import pytest

import veyra_engine
from veyra_common.envelope import stamp
from veyra_common.models import DlqRecord, Envelope, NormEvent
from veyra_engine import (
    Engine,
    EngineContext,
    backtest,
    decode,
    extract_tokens,
    mask,
    provenance_check,
    serialize,
    template_sig,
)

T3 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)
HINDI = "सिस्टम त्रुटि user=r.patil ip=10.4.1.20".encode()


def make_env(raw: bytes = T3, **over: object) -> Envelope:
    kwargs: dict[str, object] = {
        "collector_id": "edge-dmz-01",
        "transport": "syslog_tcp",
        "framing_method": "multiline_join",
        "source_id": "src_authsrv_01",
        "tenant_id": "t_maha_power",
        "vendor": "custom",
        "parts": 2,
    }
    kwargs.update(over)
    return stamp(raw, **kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------- frozen surface
def test_public_api_is_the_one_track_c_imports() -> None:
    for name in (
        "Engine",
        "EngineContext",
        "NormResult",
        "PeelResult",
        "Token",
        "Check",
        "BacktestResult",
        "extract_tokens",
        "template_sig",
        "provenance_check",
        "mask",
        "backtest",
        "serialize",
        "decode",
    ):
        assert hasattr(veyra_engine, name), f"IF-ENGINE-LIB requires {name}"


def test_engine_signatures_are_frozen() -> None:
    normalize = inspect.signature(Engine.normalize)
    assert list(normalize.parameters) == ["self", "envelope", "use_candidate"]
    assert normalize.parameters["use_candidate"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(Engine.load).parameters) == ["self", "compiled"]
    assert list(inspect.signature(Engine.set_candidate).parameters) == [
        "self",
        "compiled",
        "contract_id",
    ]
    assert list(inspect.signature(Engine.peel).parameters) == ["self", "envelope"]


# ---------------------------------------------------------------- invariants
def test_normalize_returns_a_valid_norm_event() -> None:
    result = Engine().normalize(make_env())
    assert 1 <= result.tier <= 4
    NormEvent.model_validate(result.ocsf)  # the shape B and C code against
    assert result.ocsf["ulpf"] is result.ulpf
    assert result.ocsf["raw_data"].startswith("<134>Sep 26")
    assert isinstance(result.ocsf["time"], int), "time must be epoch ms as an int, never a float"


def test_tier_two_or_worse_always_carries_a_dlq_record() -> None:
    """P2: nothing is dropped; a degraded parse produces a DLQ copy with a reason."""
    result = Engine().normalize(make_env())
    assert result.tier >= 2, "no contract is loaded, so this cannot be tier 1"
    assert isinstance(result.dlq, DlqRecord)
    assert result.dlq.reason_code == "no_contract"


def test_normalize_is_pure_and_deterministic() -> None:
    env = make_env()
    first = Engine().normalize(env)
    second = Engine().normalize(env)
    assert serialize(first.ocsf) == serialize(second.ocsf)


def test_normalize_never_raises_on_hostile_input() -> None:
    engine = Engine()
    for raw in (b"", b"\x00\xff\xfe", b"a" * 70000, HINDI, b"{", b'{"a":'):
        result = engine.normalize(make_env(raw))
        assert 1 <= result.tier <= 4


def test_contract_set_swaps_atomically() -> None:
    engine = Engine()
    assert engine.contracts_loaded == 0
    compiled = {"contract": "authsrv", "version": 1, "sources": ["src_authsrv_01"], "templates": []}
    engine.load([compiled])
    assert engine.contracts_loaded == 1
    assert engine.contract_for_source("src_authsrv_01") == compiled
    assert engine.contract_for_source("src_unknown") is None
    engine.load([])
    assert engine.contracts_loaded == 0


def test_candidate_set_and_clear() -> None:
    engine = Engine()
    engine.set_candidate({"contract": "authsrv", "version": 2, "templates": []}, "authsrv")
    assert engine.normalize(make_env(), use_candidate=True).ulpf["shadow"] is True
    engine.set_candidate(None, "authsrv")
    assert engine.normalize(make_env()).ulpf["shadow"] is False


def test_ulpf_carries_the_contract_reference_when_one_applies() -> None:
    engine = Engine()
    engine.load(
        [{"contract": "authsrv", "version": 3, "sources": ["src_authsrv_01"], "templates": []}]
    )
    ulpf = engine.normalize(make_env()).ulpf
    assert ulpf["contract"] == {"id": "authsrv", "version": 3}
    assert ulpf["engine_version"] == veyra_engine.__version__


# ---------------------------------------------------------------- decode
@pytest.mark.parametrize(
    ("raw", "expected_encoding"),
    [(b"plain ascii", "utf-8"), (HINDI, "utf-8")],
)
def test_decode_utf8_paths(raw: bytes, expected_encoding: str) -> None:
    decoded = decode(raw)
    assert decoded.encoding == expected_encoding
    assert decoded.invalid_bytes == 0
    assert decoded.confidence > 0.5
    assert decoded.text.encode("utf-8") == raw
    # The offset map is what makes byte-accurate provenance possible (P4).
    assert decoded.byte_span((0, len(decoded.text))) == (0, len(raw))


def test_decode_invalid_bytes_do_not_raise() -> None:
    decoded = decode(b"\xff\xfe\x00bad")
    assert isinstance(decoded.text, str) and decoded.encoding


# ---------------------------------------------------------------- tokens
def test_token_spans_always_slice_back_to_the_value() -> None:
    """The property A4's provenance_check and B6's highlighter both rely on."""
    for text in (
        "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1",
        'ts=2026-09-26T14:05:11Z msg="hello world" id=0192a4f0-0000-7000-8000-000000000001',
        "सिस्टम user=r.patil ip=10.4.1.20",
    ):
        for token in extract_tokens(text):
            assert text[token.start : token.end] == token.value, token


def test_token_ids_are_stable_and_ordered() -> None:
    text = "user=a.sharma from 103.21.4.77"
    first = extract_tokens(text)
    assert [t.id for t in first] == [f"k{i + 1}" for i in range(len(first))]
    assert first == extract_tokens(text)
    assert [t.start for t in first] == sorted(t.start for t in first)


def test_user_keys_become_user_tokens() -> None:
    tokens = {t.key: t for t in extract_tokens("user=a.sharma acct=root other=x") if t.key}
    assert tokens["user"].kind == "user"
    assert tokens["acct"].kind == "user"
    assert tokens["other"].kind == "kv_value"


def test_ip_and_quoted_tokens() -> None:
    kinds = {t.kind for t in extract_tokens('src=45.12.3.9 msg="a b" n=42')}
    assert "ip" in kinds


# ---------------------------------------------------------------- misc helpers
def test_template_sig_is_the_shared_frozen_one() -> None:
    assert template_sig(
        "authsrv", "user=neel.k FAILED login from 45.12.3.9 via 10.2.3.4 attempts:3"
    ) == ("t_3c85a1bfbf81")


def test_peel_reports_the_text_field() -> None:
    peel = Engine().peel(make_env())
    assert peel.text_field is not None
    assert peel.text_span == (0, len(peel.text_field))


def test_provenance_check_validates_spans() -> None:
    env = make_env()
    result = Engine().normalize(env)
    assert provenance_check(result.ocsf, env.raw_bytes) == [], "nothing mapped, nothing to locate"
    result.ocsf["ulpf"]["field_offsets"] = {"user.name": (10, 5000)}
    checks = provenance_check(result.ocsf, env.raw_bytes)
    assert checks and not checks[0].ok


def test_mask_keeps_ips() -> None:
    masked, _ = mask("user=a.sharma FAILED login from 103.21.4.77")
    assert "103.21.4.77" in masked


def test_backtest_shape() -> None:
    env = make_env()
    result = backtest(None, {"contract": "authsrv", "version": 2, "templates": []}, [env, env])
    assert result.n == 2
    assert result.upgraded + result.regressed + result.unchanged == 2


def test_serialize_is_byte_stable_and_sorted() -> None:
    assert serialize({"b": 1, "a": 2}) == b'{"a":2,"b":1}'
    assert serialize({"hi": "सिस्टम"}) == '{"hi":"सिस्टम"}'.encode()


def test_engine_context_defaults_come_from_the_profile_table() -> None:
    ctx = EngineContext()
    assert ctx.budget_us == 5000
    assert ctx.peel_max_depth == 4
    assert ctx.max_event_bytes == 65536
