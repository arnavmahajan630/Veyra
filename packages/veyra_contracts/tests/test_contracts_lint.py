"""Contract lint: shadowed templates, unproduced required paths, PII declarations."""

from __future__ import annotations

import re2
import yaml

from veyra_contracts import compile
from veyra_contracts.lint import has_errors, lint
from veyra_contracts.models import ContractYaml
from veyra_contracts.pattern import compile_pattern, example_text

HEAD = """\
contract: c
version: 1
tenant: t_demo
"""


def _lint(body: str) -> list[tuple[str, str, str | None]]:
    text = HEAD + body
    spec = ContractYaml.model_validate(yaml.safe_load(text))
    return [(f.level, f.code, f.template) for f in lint(spec, compile(text))]


def test_example_text_matches_its_own_pattern() -> None:
    for pattern in (
        "user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<n:int>",
        "GET <path:quoted> took <ms:int>ms <*>   tail <rest:rest>",
        r"literal \<angle\> <w:word>",
    ):
        regex, _ = compile_pattern(pattern)
        assert re2.search(regex, example_text(pattern)), pattern


def test_a_broader_earlier_template_shadows_a_later_one() -> None:
    findings = _lint(
        """\
templates:
  - {id: any_login, pattern: 'user=<user> <status> login', class: authentication, activity: logon}
  - {id: ok_login, pattern: 'user=<user> OK login', class: authentication, activity: logon}
"""
    )
    assert ("error", "shadowed", "ok_login") in findings


def test_a_narrower_earlier_template_does_not_shadow() -> None:
    findings = _lint(
        """\
templates:
  - id: failed
    pattern: 'Failed password for <user> from <ip:ip> port <port:int> ssh2'
    class: authentication
    activity: logon
  - id: failed_invalid
    pattern: 'Failed password for invalid user <user> from <ip:ip> port <port:int> ssh2'
    class: authentication
    activity: logon
"""
    )
    assert not [f for f in findings if f[1] == "shadowed"]


def test_required_paths_must_be_produced() -> None:
    body = """\
templates:
  - {id: a, pattern: 'x <u>', class: authentication, activity: logon, map: {user.name: $u}}
  - {id: b, pattern: 'y <v>', class: authentication, activity: logoff, map: {message: $v}}
required: [time, user.name, src_endpoint.ip]
"""
    findings = _lint(body)
    # src_endpoint.ip: nobody produces it (error, reported once, not per template)
    assert findings.count(("error", "required_unproduced", None)) == 1
    # user.name: only b lacks it
    assert ("warning", "required_missing", "b") in findings
    assert ("warning", "required_missing", "a") not in findings
    spec = ContractYaml.model_validate(yaml.safe_load(HEAD + body))
    assert has_errors(lint(spec, compile(HEAD + body)))


def test_pii_declarations_are_checked_both_ways() -> None:
    findings = _lint(
        """\
templates:
  - {id: a, pattern: 'x <u>', class: authentication, activity: logon, map: {user.name: $u}}
pii: [src_endpoint.ip]
"""
    )
    assert ("warning", "pii_unmapped", None) in findings
    assert ("warning", "pii_undeclared", "a") in findings


def test_a_clean_contract_has_no_findings() -> None:
    assert (
        _lint(
            """\
templates:
  - {id: a, pattern: 'x <u>', class: authentication, activity: logon, map: {user.name: $u}}
required: [time, user.name]
pii: [user.name]
"""
        )
        == []
    )
