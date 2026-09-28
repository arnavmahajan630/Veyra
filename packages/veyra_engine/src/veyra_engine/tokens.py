"""``extract_tokens`` — every interesting value in a line, with its exact span.

This is the input to three different consumers, which is why it is its own module and why the span
property is tested to death:

* **tier 3** (A4) turns typed tokens into observables and byte offsets, so a log nobody wrote a
  contract for still arrives with its IPs and users findable;
* **the LLM drafter** (C4) may only point at token *ids* — never write values — which is what makes
  a hallucinated mapping impossible by construction (D10);
* **the console** (B6) highlights the spans.

Two invariants hold for every token: ``text[token.start:token.end] == token.value``, and the ids are
stable for identical input (``k1``, ``k2``, … in document order), because a draft cached against
``template_sig`` must resolve to the same tokens later.

RE2 only (A4's rule): patterns are linear-time, so a hostile line cannot make this hang.
"""

from __future__ import annotations

import re2

from veyra_engine.types import Token, TokenKind

# Keys whose value is a user identity. Checked case-insensitively against the key name.
USER_KEYS = frozenset({"user", "username", "usr", "account", "acct", "login", "uid", "userid"})

# Prose that introduces a user: "Failed password for invalid user admin", "closed for r.patil",
# "session opened for user x by (uid=0)". The word after the phrase is the candidate.
_RE_USER_PROSE = re2.compile(
    r"(?:\bfor\s+(?:invalid\s+)?user\b|\bfor\s+user\b|\buser\b|\bfor\b|\bby\b|\baccount\b)"
    r"\s+(?P<user>[A-Za-z_][\w.\-]{1,63})"
)

# key=value and key:value, with quoted values kept whole. RE2 has no negative lookahead, so
# "http://host" is not excluded here by pattern — the url pattern claims it first, and overlap
# protection keeps this one off it.
_RE_KV = re2.compile(r"([A-Za-z_][\w.\-]*)\s*=\s*(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)")
_RE_CKV = re2.compile(r"([A-Za-z_][\w.\-]*):\s*(\"[^\"\n]*\"|[^\s,;:]+)")

# Ordered: the first pattern to claim a span wins, so specific shapes beat generic ones.
_PATTERNS: list[tuple[TokenKind, object]] = [
    (
        "uuid",
        re2.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
    ),
    ("url", re2.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>]+")),
    ("email", re2.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (
        "timestamp",
        re2.compile(
            r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+\-]\d{2}:?\d{2})?"
            r"|\b\d{2}[-/]\d{2}[-/]\d{4}[T ]\d{2}:\d{2}:\d{2}\b"
            r"|\b[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"
        ),
    ),
    # IPv4, optionally with :port — the port is split out into its own token below.
    ("ip", re2.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b(?::\d{1,5}\b)?")),
    (
        "ipv6",
        re2.compile(
            r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
            r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?"
        ),
    ),
    ("hash", re2.compile(r"\b[0-9a-fA-F]{32}\b|\b[0-9a-fA-F]{40}\b|\b[0-9a-fA-F]{64}\b")),
    ("quoted", re2.compile(r'"[^"\n]*"')),
    ("kv_value", _RE_KV),
    ("ckv_value", _RE_CKV),
    # A hostname needs a dot and a letter-led label, so it is not confused with a decimal.
    ("hostname", re2.compile(r"\b[A-Za-z][\w\-]*(?:\.[A-Za-z][\w\-]*)+\b")),
    ("port", re2.compile(r"\bport\s+(\d{1,5})\b")),
    ("int", re2.compile(r"-?\b\d+\b")),
    ("word", re2.compile(r"\b[A-Za-z][\w.\-]*\b")),
]

# Kinds that are worth an observable / a draft reference. `word` and `int` are noise on their own.
INTERESTING: frozenset[TokenKind] = frozenset(
    {"ip", "ipv6", "email", "url", "hostname", "user", "hash", "uuid", "timestamp"}
)


# A line with 1000 key=value pairs does not need 1000 tokens: the drafter shows a handful, the
# console highlights a handful, and 512 tokens cost 15 ms to classify — three times the budget.
# The cap also stops the *scan*, not just the claiming: on a 60 KB line of `a=` pairs the `word`
# pattern alone produced 30k matches and 77 ms of Python match objects. Tokens are claimed in
# document order, so a truncated line keeps the ones a human reads first.
MAX_TOKENS = 192

# Two more bounds, both measured rather than guessed (A4 AC4). On a 60 KB line of `a=` pairs the
# `word` pattern alone produced 30k matches and 77 ms of Python match objects, and simply scanning
# 60 KB cost ~8 ms per pattern even when it matched once. So:
#   * only the first TOKENIZE_MAX_CHARS characters are tokenized — a message longer than this is
#     noise past that point for a human, the drafter and the console alike, and `raw_data` still
#     carries every byte;
#   * each pattern stops after MAX_MATCHES_PER_PATTERN matches, which bounds repetition.
# Normal log lines are a few hundred characters, so neither bound touches them.
TOKENIZE_MAX_CHARS = 4096
MAX_MATCHES_PER_PATTERN = 1000


def _claim(
    found: list[tuple[int, int, TokenKind, str, str | None]],
    occupied: bytearray,
    start: int,
    end: int,
    kind: TokenKind,
    value: str,
    key: str | None = None,
) -> bool:
    """Record a token if its span is still free.

    Occupancy is a byte-per-character map rather than a list of spans: the list version was an
    O(n^2) scan that took 130 ms on a 60 KB line of `a=` pairs, over the whole per-event budget.
    """
    if end <= start or len(found) >= MAX_TOKENS:
        return False
    if end > len(occupied):
        return False
    # `any` over a slice short-circuits and is a C-level scan, so this stays cheap.
    if any(occupied[start:end]):
        return False
    found.append((start, end, kind, value, key))
    occupied[start:end] = b"\x01" * (end - start)
    return True


def extract_tokens(text: str) -> list[Token]:
    """Ordered tokens with exact spans. Ids are stable for identical ``text``.

    Bounded work: see ``TOKENIZE_MAX_CHARS`` and ``MAX_MATCHES_PER_PATTERN``. Spans are always in
    the coordinates of the ``text`` passed in, so a truncated window never shifts an offset.
    """
    if len(text) > TOKENIZE_MAX_CHARS:
        text = text[:TOKENIZE_MAX_CHARS]
    found: list[tuple[int, int, TokenKind, str, str | None]] = []
    occupied = bytearray(len(text))

    # 1. Users first: a user is a *role*, and claiming it early stops the value being taken as a
    #    plain word or kv_value. Keys first, then prose.
    for match in _RE_KV.finditer(text):
        if len(found) >= MAX_TOKENS:
            break
        key = match.group(1)
        if key.lower() not in USER_KEYS:
            continue
        start, end = match.span(2)
        value = match.group(2)
        if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
            start, end, value = start + 1, end - 1, value[1:-1]
        _claim(found, occupied, start, end, "user", value, key)
    for match in _RE_CKV.finditer(text):  # the key:value form
        if len(found) >= MAX_TOKENS:
            break
        key = match.group(1)
        if key.lower() not in USER_KEYS:
            continue
        start, end = match.span(2)
        _claim(found, occupied, start, end, "user", match.group(2), key)
    for match in _RE_USER_PROSE.finditer(text):
        if len(found) >= MAX_TOKENS:
            break
        index = _RE_USER_PROSE.groupindex["user"]
        start, end = match.span(index)
        value = match.group(index)
        # "for 3 attempts" or "by 10.0.0.1" are not users.
        if value.isdigit() or value.lower() in {"user", "invalid", "password", "uid"}:
            continue
        _claim(found, occupied, start, end, "user", value)

    # 2. Everything else, specific shapes before generic ones.
    for kind, pattern in _PATTERNS:
        if len(found) >= MAX_TOKENS:
            break
        examined = 0
        for match in pattern.finditer(text):  # type: ignore[attr-defined]
            examined += 1
            if len(found) >= MAX_TOKENS or examined > MAX_MATCHES_PER_PATTERN:
                break
            if kind == "ip":
                whole = match.group()
                start, end = match.span()
                host, _, port = whole.partition(":")
                if _claim(found, occupied, start, start + len(host), "ip", host) and port:
                    port_start = start + len(host) + 1
                    _claim(found, occupied, port_start, port_start + len(port), "port", port)
                continue

            if kind in ("kv_value", "ckv_value"):
                key = match.group(1)
                start, end = match.span(2)
                value = match.group(2)
                if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                    start, end, value = start + 1, end - 1, value[1:-1]
                # A kv value that is itself a recognisable shape keeps that kind, so
                # `src=45.12.3.9` yields an ip token rather than an opaque kv_value.
                inner = _classify_value(value)
                _claim(found, occupied, start, end, inner or "kv_value", value, key)
                continue

            if kind == "port":
                start, end = match.span(1)
                _claim(found, occupied, start, end, "port", match.group(1))
                continue

            if kind == "quoted":
                start, end = match.span()
                _claim(found, occupied, start + 1, end - 1, "quoted", match.group()[1:-1])
                continue

            start, end = match.span()
            _claim(found, occupied, start, end, kind, match.group())

    found.sort(key=lambda item: (item[0], item[1]))
    return [
        Token(id=f"k{index + 1}", value=value, start=start, end=end, kind=kind, key=key)
        for index, (start, end, kind, value, key) in enumerate(found)
    ]


_RE_IS_IP = re2.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_RE_IS_IPV6 = re2.compile(r"^(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f:]*$")
_RE_IS_EMAIL = re2.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_RE_IS_URL = re2.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://\S+$")
_RE_IS_UUID = re2.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RE_IS_HASH = re2.compile(r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_RE_IS_TS = re2.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_RE_IS_HOSTNAME = re2.compile(r"^[A-Za-z][\w\-]*(?:\.[A-Za-z][\w\-]*)+$")
_RE_IS_INT = re2.compile(r"^-?\d+$")


def _classify_value(value: str) -> TokenKind | None:
    """The kind of a kv value, so `src=1.2.3.4` is an ip and not an opaque pair.

    Cheap rejection first: every interesting shape contains a dot, a colon, an ``@`` or is all
    digits, so a plain word skips ten regex calls. With a few hundred pairs on a line that is the
    difference between 15 ms and 2 ms.
    """
    if not value:
        return None
    if not (
        "." in value
        or ":" in value
        or "@" in value
        or value.lstrip("-").isdigit()
        or (len(value) >= 32 and _RE_IS_HASH.match(value))
    ):
        return None
    if _RE_IS_IP.match(value):
        return "ip"
    if _RE_IS_UUID.match(value):
        return "uuid"
    if _RE_IS_EMAIL.match(value):
        return "email"
    if _RE_IS_URL.match(value):
        return "url"
    if _RE_IS_HASH.match(value):
        return "hash"
    if _RE_IS_TS.match(value):
        return "timestamp"
    if _RE_IS_IPV6.match(value) and value.count(":") >= 2:
        return "ipv6"
    if _RE_IS_HOSTNAME.match(value):
        return "hostname"
    if _RE_IS_INT.match(value):
        return "int"
    return None


def tokens_by_kind(tokens: list[Token]) -> dict[TokenKind, list[Token]]:
    """Group tokens by kind, preserving document order within each kind."""
    grouped: dict[TokenKind, list[Token]] = {}
    for token in tokens:
        grouped.setdefault(token.kind, []).append(token)
    return grouped
