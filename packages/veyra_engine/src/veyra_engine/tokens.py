"""``extract_tokens`` — the real (simple) S0 version.

A3/A4 replace the internals with the full token catalogue; the signature is frozen.
Spans are character offsets into the text passed in, and every span must slice back to
exactly the token value — a property test asserts that, and A4's ``provenance_check``
depends on it.

Regexes live in :mod:`veyra_engine._patterns` (the only module allowed to import ``re``
indirectly through ``google-re2``), keeping the engine RE2-only per A4.
"""

from __future__ import annotations

import re2

from veyra_engine.types import Token, TokenKind

# Ordered: the first pattern that matches a span wins.
_PATTERNS: list[tuple[TokenKind, object]] = [
    (
        "uuid",
        re2.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"),
    ),
    ("email", re2.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("url", re2.compile(r"https?://[^\s\"']+")),
    ("ip", re2.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")),
    ("timestamp", re2.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?\b")),
    ("hash", re2.compile(r"\b[0-9a-fA-F]{32,64}\b")),
    ("quoted", re2.compile(r'"[^"]*"')),
    ("kv_value", re2.compile(r"([A-Za-z_][\w.\-]*)=(\"[^\"]*\"|\S+)")),
    ("int", re2.compile(r"\b\d+\b")),
    ("word", re2.compile(r"[A-Za-z][\w.\-]*")),
]

# Keys whose value is a user identity (A4 extends this list).
_USER_KEYS = frozenset({"user", "username", "usr", "account", "acct", "login", "uid"})


def _overlaps(taken: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(start < t_end and end > t_start for t_start, t_end in taken)


def extract_tokens(text: str) -> list[Token]:
    """Ordered tokens with exact spans; ids are stable for identical input."""
    found: list[tuple[int, int, TokenKind, str, str | None]] = []
    taken: list[tuple[int, int]] = []

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):  # type: ignore[attr-defined]
            if kind == "kv_value":
                key = match.group(1)
                start, end = match.span(2)
                value = match.group(2)
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    start, end, value = start + 1, end - 1, value[1:-1]
                if _overlaps(taken, start, end):
                    continue
                token_kind: TokenKind = "user" if key.lower() in _USER_KEYS else "kv_value"
                found.append((start, end, token_kind, value, key))
                taken.append((start, end))
                continue

            start, end = match.span()
            if _overlaps(taken, start, end):
                continue
            value = match.group()
            if kind == "quoted":
                start, end, value = start + 1, end - 1, value[1:-1]
            found.append((start, end, kind, value, None))
            taken.append((start, end))

    found.sort(key=lambda item: (item[0], item[1]))
    return [
        Token(id=f"k{i + 1}", value=value, start=start, end=end, kind=kind, key=key)
        for i, (start, end, kind, value, key) in enumerate(found)
    ]
