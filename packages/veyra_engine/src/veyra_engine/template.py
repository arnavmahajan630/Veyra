"""Compile a contract's template pattern into an anchored RE2, and match it.

The pattern syntax is IF-CONTRACT-YAML's, not a regex: contract authors write

    user=<user> FAILED login from <src_ip:ip> via <dst_ip:ip> attempts:<attempts:int>

and this module turns that into ``^user=(?P<user>\\S+)\\s+FAILED\\s+login\\s+from ...$``.
Keeping the author-facing syntax small is what makes the LLM drafter safe (D10): it can only
place captures around tokens that already exist in the sample, never invent a regex.

Two rules that matter downstream:

* literal text is regex-escaped and runs of whitespace match ``\\s+``, so a log line with two
  spaces where the sample had one still matches;
* the regex is anchored at both ends, so a template either describes the whole message or does
  not match — no accidental partial matches sneaking into tier 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import re2

# Capture types available to contract authors (IF-CONTRACT-YAML "Pattern syntax").
TOKEN_PATTERNS: dict[str, str] = {
    "": r"\S+",
    "word": r"[\w.\-]+",
    "int": r"-?\d+",
    "ip": r"(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f:]*:[0-9A-Fa-f:.]+)",
    "rest": r".*",
    "quoted": r"[^\"]*",
}
ANON_PATTERN = r"\S+"


class TemplateError(ValueError):
    """The pattern cannot be compiled — a contract bug, reported, never raised at runtime."""


@dataclass(slots=True)
class Capture:
    """One named capture in a compiled template."""

    name: str
    type: str


@dataclass(slots=True)
class CompiledTemplate:
    """A template ready to match, plus what it will capture."""

    id: str
    regex: str
    captures: list[Capture]
    pattern: Any  # the compiled re2 object
    class_uid: int = 0
    activity_id: int = 0
    type_uid: int = 0
    category: str = "uncategorized"
    map: list[dict[str, Any]] | None = None
    unmapped: list[str] | None = None


@dataclass(slots=True)
class TemplateMatch:
    """A successful match: captured values with their spans in the matched text."""

    template: CompiledTemplate
    values: dict[str, str]
    spans: dict[str, tuple[int, int]]


def compile_pattern(pattern: str) -> tuple[str, list[Capture]]:
    """Translate IF-CONTRACT-YAML pattern syntax into an anchored RE2 string."""
    out: list[str] = ["^"]
    captures: list[Capture] = []
    index = 0
    anonymous = 0
    seen: set[str] = set()

    while index < len(pattern):
        char = pattern[index]

        if char == "<":
            close = pattern.find(">", index)
            if close == -1:
                raise TemplateError(f"unclosed '<' at position {index}")
            token = pattern[index + 1 : close]
            index = close + 1

            if token == "*":
                anonymous += 1
                out.append(f"(?:{ANON_PATTERN})")
                continue

            name, _, kind = token.partition(":")
            if not name:
                raise TemplateError(f"empty capture name in <{token}>")
            if kind not in TOKEN_PATTERNS:
                raise TemplateError(f"unknown capture type {kind!r} in <{token}>")
            if name in seen:
                raise TemplateError(f"duplicate capture name {name!r}")
            seen.add(name)
            captures.append(Capture(name=name, type=kind or "token"))

            if kind == "quoted":
                # The quotes are literal; the capture is what is between them.
                out.append(f'"(?P<{name}>{TOKEN_PATTERNS[kind]})"')
            else:
                out.append(f"(?P<{name}>{TOKEN_PATTERNS[kind]})")
            continue

        if char in " \t":
            while index < len(pattern) and pattern[index] in " \t":
                index += 1
            out.append(r"\s+")
            continue

        out.append(re2.escape(char))
        index += 1

    out.append("$")
    return "".join(out), captures


def compile_template(spec: dict[str, Any], *, class_resolver: Any = None) -> CompiledTemplate:
    """Compile one ``templates:`` entry from a contract.

    ``spec`` may already carry a ``regex`` (a contract compiled by C2 does), in which case the
    pattern step is skipped and the regex is used as-is — so runtime never depends on the
    author-facing syntax.
    """
    template_id = str(spec.get("id") or "unnamed")
    if spec.get("regex"):
        regex = str(spec["regex"])
        captures = [
            Capture(name=c["name"], type=c.get("type", "token"))
            if isinstance(c, dict)
            else Capture(name=str(c), type="token")
            for c in spec.get("captures", [])
        ]
    else:
        pattern = spec.get("pattern")
        if not pattern:
            raise TemplateError(f"template {template_id!r} has neither pattern nor regex")
        regex, captures = compile_pattern(str(pattern))

    try:
        compiled = re2.compile(regex)
    except Exception as exc:  # a bad pattern is a contract bug, surfaced not raised
        raise TemplateError(f"template {template_id!r} does not compile: {exc}") from exc

    if not captures:
        captures = [Capture(name=name, type="token") for name in compiled.groupindex]

    return CompiledTemplate(
        id=template_id,
        regex=regex,
        captures=captures,
        pattern=compiled,
        class_uid=int(spec.get("class_uid", 0) or 0),
        activity_id=int(spec.get("activity_id", 0) or 0),
        type_uid=int(spec.get("type_uid", 0) or 0),
        category=str(spec.get("category", "uncategorized")),
        map=spec.get("map"),
        unmapped=spec.get("unmapped"),
    )


def match_templates(
    templates: list[CompiledTemplate],
    text: str,
    *,
    offset: int = 0,
) -> TemplateMatch | None:
    """First template that matches wins (IF-CONTRACT-YAML: templates are tried in order).

    ``offset`` is added to every span, so callers pass the text field's position inside the raw
    event and get spans that are already in raw-text coordinates.
    """
    for template in templates:
        match = template.pattern.match(text)
        if not match:
            continue
        values: dict[str, str] = {}
        spans: dict[str, tuple[int, int]] = {}
        for capture in template.captures:
            index = template.pattern.groupindex.get(capture.name)
            if index is None:
                continue
            value = match.group(index)
            if value is None:
                continue
            start, end = match.span(index)
            values[capture.name] = value
            if start >= 0:
                spans[capture.name] = (start + offset, end + offset)
        return TemplateMatch(template=template, values=values, spans=spans)
    return None
