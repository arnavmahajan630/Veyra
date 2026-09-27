"""IF-CONTRACT-YAML pattern syntax → one anchored RE2 regex with named groups.

``\\<`` and ``\\>`` are literal angle brackets (decision TC3); every other ``<…>`` must
be a valid token. Columns in errors are 1-based positions inside the pattern string.
"""

from __future__ import annotations

from dataclasses import dataclass

import re2

from veyra_contracts.errors import ContractError


@dataclass(frozen=True, slots=True)
class Capture:
    name: str
    type: str


# token type after ':' -> (capture type name, regex body)
_TYPES: dict[str, tuple[str, str]] = {
    "": ("string", r"\S+"),
    "ip": ("ip", r"\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]*:[0-9A-Fa-f:.]+"),
    "int": ("int", r"-?\d+"),
    "word": ("word", r"[\w.\-]+"),
    "rest": ("rest", r".*"),
    "quoted": ("quoted", r'[^"]*'),
}
_SPECIAL = frozenset("\\.^$*+?()[]{}|-/")


def compile_pattern(pattern: str) -> tuple[str, list[Capture]]:
    parts: list[str] = ["^"]
    captures: list[Capture] = []
    literal: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern) and pattern[i + 1] in "<>\\":
            literal.append(pattern[i + 1])
            i += 2
            continue
        if ch == "<":
            end = pattern.find(">", i + 1)
            if end == -1:
                raise ContractError(f"unterminated '<' at pattern column {i + 1}", column=i + 1)
            parts.append(_literal_regex("".join(literal)))
            literal.clear()
            parts.append(_token_regex(pattern[i + 1 : end], i + 1, captures))
            i = end + 1
            continue
        literal.append(ch)
        i += 1
    parts.append(_literal_regex("".join(literal)))
    parts.append("$")
    regex = "".join(parts)
    try:
        re2.compile(regex)
    except Exception as exc:  # re2 raises its own error type
        raise ContractError(f"pattern is not valid RE2: {exc}") from exc
    return regex, captures


def _literal_regex(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i].isspace():
            while i < len(text) and text[i].isspace():
                i += 1
            out.append(r"\s+")
            continue
        out.append("\\" + text[i] if text[i] in _SPECIAL else text[i])
        i += 1
    return "".join(out)


def _token_regex(body: str, column: int, captures: list[Capture]) -> str:
    if body == "*":
        return r"\S+"
    name, _, kind = body.partition(":")
    if not (name.isascii() and name.isidentifier()) or name.startswith("__"):
        raise ContractError(
            f"invalid capture name {name!r} in <{body}> at pattern column {column}", column=column
        )
    if kind not in _TYPES:
        allowed = sorted(k for k in _TYPES if k)
        raise ContractError(
            f"unknown capture type {kind!r} in <{body}>; expected one of {allowed}", column=column
        )
    if any(c.name == name for c in captures):
        raise ContractError(f"duplicate capture <{name}> at pattern column {column}", column=column)
    type_name, body_regex = _TYPES[kind]
    captures.append(Capture(name=name, type=type_name))
    if kind == "quoted":
        return f'"(?P<{name}>{body_regex})"'
    return f"(?P<{name}>{body_regex})"
