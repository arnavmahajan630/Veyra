"""Structural validation of IF-CONTRACT-YAML (semantics are the compiler's job)."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from veyra_contracts.models import ConstValue, ContractYaml, TsValue, VocabValue

AUTHSRV = """
contract: authsrv
version: 3
tenant: t_maha_power
sources: [src_authsrv_01]
description: Maha Power auth server
state: active
envelope:
  - syslog: {variant: auto}
  - json: {text_field: msg}
time:
  field: syslog.timestamp
  formats: ["%b %d %H:%M:%S"]
  timezone: Asia/Kolkata
  year: infer_from_received
templates:
  - id: auth_failed
    pattern: 'user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>'
    class: authentication
    activity: logon
    map:
      user.name: $user
      status_id: {const: 2}
      severity_id: {vocab: severity_words, from: $user}
      time: {ts: $attempts, formats: ["%s"]}
    unmapped: [attempts]
required: [time, user.name]
vocab: [severity_words]
tests:
  - sample: samples/authsrv/failed_1.log
    expect: expected/authsrv/failed_1.json
provenance:
  drafted_by: llm:qwen2.5:3b
  approved_by: [author@maha, approver@veyra]
"""


def load(text: str = AUTHSRV) -> ContractYaml:
    return ContractYaml.model_validate(yaml.safe_load(text))


def test_the_if_example_validates_and_parses_map_value_forms() -> None:
    spec = load()
    assert spec.contract == "authsrv" and spec.version == 3
    template = spec.templates[0]
    assert template.class_ == "authentication"
    assert template.map["user.name"] == "$user"
    assert template.map["status_id"] == ConstValue(const=2)
    assert template.map["severity_id"] == VocabValue(
        vocab="severity_words",
        from_="$user",  # type: ignore[call-arg]
    )
    assert template.map["time"] == TsValue(ts="$attempts", formats=["%s"])


def test_unknown_top_level_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="colour"):
        load(AUTHSRV + "colour: red\n")


def test_unknown_envelope_layer_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown layer 'xml'"):
        load(AUTHSRV.replace("- json: {text_field: msg}", "- xml: {}"))


def test_map_string_must_be_a_reference() -> None:
    with pytest.raises(ValidationError, match="must start with"):
        load(AUTHSRV.replace("user.name: $user", "user.name: user"))


def test_duplicate_template_ids_are_rejected() -> None:
    doubled = AUTHSRV.replace(
        "required:",
        "  - {id: auth_failed, pattern: x, class: authentication, activity: logon}\nrequired:",
    )
    with pytest.raises(ValidationError, match="duplicate template ids"):
        load(doubled)


def test_ids_follow_if_naming() -> None:
    with pytest.raises(ValidationError):
        load(AUTHSRV.replace("tenant: t_maha_power", "tenant: maha"))
    with pytest.raises(ValidationError):
        load(AUTHSRV.replace("sources: [src_authsrv_01]", "sources: [authsrv]"))
