"""Pattern syntax (IF-CONTRACT-YAML table) → anchored RE2 with named groups."""

from __future__ import annotations

import pytest
import re2

from veyra_contracts.errors import ContractError
from veyra_contracts.pattern import Capture, compile_pattern

T3 = "user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>"


def match(pattern: str, text: str) -> dict[str, str] | None:
    regex, _ = compile_pattern(pattern)
    m = re2.compile(regex).search(text)
    return None if m is None else m.groupdict()


def test_t3_pattern_captures_the_real_values() -> None:
    assert match(T3, "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1") == {
        "user": "a.sharma",
        "src_ip": "103.21.4.77",
        "dst_ip": "10.2.3.4",
        "attempts": "1",
    }


def test_captures_are_reported_in_order_with_types() -> None:
    _, captures = compile_pattern(T3)
    assert captures == [
        Capture("user", "string"),
        Capture("src_ip", "ip"),
        Capture("dst_ip", "ip"),
        Capture("attempts", "int"),
    ]


def test_the_regex_is_anchored_at_both_ends() -> None:
    assert match("a <x>", "a b") == {"x": "b"}
    assert match("a <x>", "zz a b") is None
    assert match("a <x>", "a b c") is None


def test_whitespace_runs_match_any_whitespace() -> None:
    assert match("a  b", "a b") == {}
    assert match("a b", "a\t\t b") == {}


def test_typed_tokens() -> None:
    assert match("<v:int>", "-42") == {"v": "-42"}
    assert match("<v:int>", "4x") is None
    assert match("<v:word>", "host-1.example_a") == {"v": "host-1.example_a"}
    assert match("msg <v:rest>", "msg anything at all") == {"v": "anything at all"}
    assert match("<v:quoted>", '"two words"') == {"v": "two words"}
    assert match("<v:ip>", "fe80::1") == {"v": "fe80::1"}
    assert match("<v:ip>", "103.21.4.77") == {"v": "103.21.4.77"}
    assert match("<v:ip>", "not-an-ip") is None
    assert match("<v:ip>", "999.999.999.999") is None
    assert match("<v:ip>", "aa:bb:cc:dd:ee:ff") is None
    assert match("<v:ip>", "::1") == {"v": "::1"}
    assert match("<v:ip>", "2001:db8:0:0:0:0:2:1") == {"v": "2001:db8:0:0:0:0:2:1"}
    assert match("<v:ip>", "255.255.255.255") == {"v": "255.255.255.255"}


def test_anonymous_token_is_not_a_capture() -> None:
    regex, captures = compile_pattern("id <*> end")
    assert captures == []
    assert re2.compile(regex).search("id 7781 end") is not None


def test_regex_metacharacters_in_literals_match_literally() -> None:
    assert match("GET /a.b?(x) <path>", "GET /a.b?(x) /index") == {"path": "/index"}
    assert match("GET /a.b?(x) <path>", "GET /aXb?(x) /index") is None


def test_escaped_angle_brackets_are_literal() -> None:
    assert match(r"\<134\> <msg:rest>", "<134> hello world") == {"msg": "hello world"}


def test_non_ascii_literals_compile_and_match() -> None:
    assert match("तापमान=<value:int>", "तापमान=42") == {"value": "42"}


@pytest.mark.parametrize(
    ("pattern", "message", "column"),
    [
        ("a <user", "unterminated", 3),
        ("<v:float>", "unknown capture type 'float'", 1),
        ("<a:>", "unknown capture type ''", 1),
        ("<a> <a>", "duplicate capture <a>", 5),
        ("<1bad>", "invalid capture name '1bad'", 1),
        ("<__text>", "invalid capture name '__text'", 1),
    ],
)
def test_bad_patterns_raise_with_a_column(pattern: str, message: str, column: int) -> None:
    with pytest.raises(ContractError, match=message) as info:
        compile_pattern(pattern)
    assert info.value.column == column
