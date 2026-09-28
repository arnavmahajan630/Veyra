"""Request building (C4): layers, tokens, which tokens vary, what the model sees."""

from __future__ import annotations

from pathlib import Path

from veyra_common.framing import split_lines
from veyra_contracts.drafting.classify import classify, peel
from veyra_contracts.drafting.request import build, display_template
from veyra_contracts.drafting.schema import DraftResponse, problems

CORPUS = Path(__file__).resolve().parents[3] / "demo" / "corpus"
T3_SIG = "t_3c85a1bfbf81"


def lines(name: str, count: int = 8) -> list[bytes]:
    return [f.raw for f in split_lines((CORPUS / name).read_bytes())][:count]


def test_t3_is_syslog_wrapping_json_with_the_text_in_msg() -> None:
    raw = lines("authsrv_t3_failed.log", 1)[0]
    layers = classify(raw.decode())
    assert layers == [{"syslog": {"variant": "auto"}}, {"json": {"text_field": "msg"}}]
    peeled = peel(raw, layers)
    assert peeled.template_text == (
        "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"
    )
    assert peeled.text[peeled.template_start :].startswith("user=a.sharma")


def test_cef_and_plain_lines() -> None:
    cef = lines("acme_ngfw_cef.log", 1)[0].decode()
    assert classify(cef) == [{"cef": {}}]
    assert classify("just some words 42") == []


def test_value_kinds_and_varying_words_are_variable() -> None:
    prepared = build(lines("authsrv_t3_failed.log"), template_sig=T3_SIG)
    variable = {prepared.token(i).value for i in prepared.variable}
    # the user, both IPs (10.2.3.4 is constant but an IP), and the attempt count
    assert variable == {"a.sharma", "103.21.4.77", "10.2.3.4", "1"}
    assert display_template(prepared) == "user=<*> FAILED login from <*> via <*> attempts:<*>"


def test_the_model_sees_masked_users_and_only_variable_tokens() -> None:
    prepared = build(lines("authsrv_t3_failed.log"), template_sig=T3_SIG)
    request = prepared.request
    assert request["template_sig"] == T3_SIG
    assert [t["value"] for t in request["tokens"]] == ["<USER_1>", "103.21.4.77", "10.2.3.4", "1"]
    assert all("a.sharma" not in s for s in request["samples_masked"])
    assert request["allowed_classes"]["authentication"] == ["logoff", "logon"]
    assert request["enums"]["status_id"]["2"] == "Failure"
    assert "user.name" in request["allowed_fields"]


def test_problems_catch_everything_outside_the_vocabulary() -> None:
    response = DraftResponse.model_validate(
        {
            "class": "authentication",
            "activity": "logon",
            "mappings": [
                {"ocsf_path": "user.name", "token": "k2"},
                {"ocsf_path": "user.name", "token": "k6"},
                {"ocsf_path": "status_id", "const": 7},
                {"ocsf_path": "made.up", "token": "k99"},
            ],
        }
    )
    found = problems(response, {"k2", "k6"})
    assert "user.name is mapped twice" in found
    assert any("status_id: const 7" in p for p in found)
    assert any("made.up is not in the OCSF field catalogue" in p for p in found)
    assert any("token 'k99'" in p for p in found)
    ok = DraftResponse.model_validate(
        {"class": "authentication", "activity": "logon",
         "mappings": [{"ocsf_path": "user.name", "token": "k2"}]}
    )  # fmt: skip
    assert problems(ok, {"k2"}) == []
