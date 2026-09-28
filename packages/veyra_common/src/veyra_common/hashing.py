"""Hashing helpers and the frozen IF-TEMPLATE-SIG reference implementation.

This module is the single source of truth for ``template_sig``; ``veyra_engine``
re-exports it, which is also why the engine's "RE2 only" rule (A4) is satisfied:
the masking regexes live here, not in the engine.

IF-TEMPLATE-SIG is **frozen** after S0. Its behaviour is pinned by the vectors in
docs/plan/reference/spec_vectors.py, asserted in tests/test_hashing.py.
"""

from __future__ import annotations

import hashlib
import re
from typing import Final

UNIT_SEP: Final = "\x1f"

_RE_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_RE_IPV4 = re.compile(r"^(\d{1,3}\.){3}\d{1,3}(:\d{1,5})?$")
_RE_IPV6 = re.compile(r"^[0-9a-fA-F:]*:[0-9a-fA-F:]*:[0-9a-fA-F:]*$")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_RE_KV = re.compile(r"^([A-Za-z_][\w.\-]*)=(.*)$")
_RE_CKV = re.compile(r"^([A-Za-z_][\w.\-]*):(.+)$")
_RE_TS = re.compile(r"^\d{1,4}[-/:T]\d{1,2}([-/:T.]\d{1,4})*Z?$")
_RE_HEX = re.compile(r"^[0-9a-fA-F]{8,}$")
_RE_DIGIT = re.compile(r"\d")


def sha256_hex(data: bytes) -> str:
    """Lowercase hex SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> bytes:
    """Raw 32-byte SHA-256 digest of ``data``."""
    return hashlib.sha256(data).digest()


def mask_token(token: str) -> str:
    """Mask one whitespace-delimited token per IF-TEMPLATE-SIG step 2.

    Rules are tried in order and the first match wins. **The output is frozen** — the vectors in
    docs/plan/reference/spec_vectors.py pin it — so the cheap character checks below are guards that
    skip regexes which could not possibly match, never a change in behaviour. They exist because a
    4 KB line has ~2000 tokens and running nine regexes on each was the single largest cost in the
    whole pipeline (A4 AC4).
    """
    if not token:
        return token

    has_digit = any(character.isdigit() for character in token)
    first = token[0]

    # 1. UUID: fixed length 36, and hyphenated.
    if len(token) == 36 and "-" in token and _RE_UUID.match(token):
        return "<UUID>"
    # 2. IPv4, optionally with :port — must start with a digit and contain a dot.
    if has_digit and first.isdigit() and "." in token and _RE_IPV4.match(token):
        return "<IP>"
    # 3. Timestamp-like: always starts with a digit.
    if has_digit and first.isdigit() and _RE_TS.match(token):
        return "<TS>"
    # 4. IPv6 needs colons before the pattern is worth trying.
    if ":" in token and ("::" in token or token.count(":") >= 3) and _RE_IPV6.match(token):
        return "<IP>"
    # 5. Email needs an @.
    if "@" in token and _RE_EMAIL.match(token):
        return "<EMAIL>"
    # 6/7. key=value and key:value.
    if "=" in token:
        m = _RE_KV.match(token)
        if m:
            return m.group(1) + "=<V>"
    if ":" in token:
        m = _RE_CKV.match(token)
        if m:
            return m.group(1) + ":<V>"
    # 8. Hex run of 8 or more.
    if len(token) >= 8 and _RE_HEX.match(token):
        return "<HEX>"
    # 9. Anything else containing a digit.
    if has_digit:
        return "<NUM>"
    return token


def mask_text(text: str) -> str:
    """Whitespace-split ``text``, mask every token, rejoin with single spaces."""
    return " ".join(mask_token(t) for t in text.split())


def template_sig(scope: str, text: str) -> str:
    """Deterministic template signature (IF-TEMPLATE-SIG, frozen).

    ``scope`` is the contract id, else the vendor, else ``"unregistered"``.
    Returns ``"t_<12 hex>"``.
    """
    masked = mask_text(text)
    digest = hashlib.sha256((scope + UNIT_SEP + masked).encode("utf-8")).hexdigest()
    return "t_" + digest[:12]


def template_sig_parts(scope: str, text: str) -> tuple[str, str]:
    """``(masked_text, sig)`` — used by tests, drift review and the console."""
    masked = mask_text(text)
    digest = hashlib.sha256((scope + UNIT_SEP + masked).encode("utf-8")).hexdigest()
    return masked, "t_" + digest[:12]
