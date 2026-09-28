"""Deterministic PII masking for DLQ text and LLM prompts (A4's final version).

Two consumers, both of which make determinism non-negotiable: the DLQ text feeds Drain3 clustering
(C3), and the same text is the LLM cache key (C4, keyed by ``template_sig``). If masking wandered
between runs, identical events would cluster separately and the cache would miss.

What gets replaced, in this order:

* values of secret-like keys → ``<SECRET>``
* emails → ``<EMAIL_n>``
* long hex or base64 blobs → ``<BLOB>``
* user identities → ``<USER_n>``

**IPs are deliberately kept.** They are not secrets, and the drafter cannot propose
``src_endpoint.ip`` if the address has been redacted out of the sample (IF-LLM-DRAFT). The same goes
for ports, hostnames and timestamps.

Numbering is per distinct value in order of first appearance, so the same user is always
``<USER_1>`` within one line.
"""

from __future__ import annotations

import re2

from veyra_engine.tokens import USER_KEYS

# Keys whose value is a credential. Key names only — the value is whatever follows.
_SECRET_KEY_WORDS = (
    "pass",
    "passwd",
    "password",
    "pwd",
    "token",
    "secret",
    "apikey",
    "api_key",
    "key",
    "session",
    "sessionid",
    "cookie",
    "auth",
    "authorization",
    "credential",
    "signature",
)
_RE_SECRET_KV = re2.compile(
    r"(?i)\b(" + "|".join(_SECRET_KEY_WORDS) + r")(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
_RE_EMAIL = re2.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A blob is a long opaque run: 24+ hex, or 24+ base64-ish characters. Long enough not to catch a
# hostname or a hash-like word a human would want to read.
_RE_HEX_BLOB = re2.compile(r"\b[0-9a-fA-F]{24,}\b")
_RE_B64_BLOB = re2.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")
# user=<value> and user:<value>, restricted to the key names tokens.py already treats as identities.
_RE_USER_KV = re2.compile(
    r"(?i)\b(" + "|".join(sorted(USER_KEYS)) + r")(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
# Prose: "for invalid user admin", "closed for r.patil", "for user bob".
_RE_USER_PROSE = re2.compile(
    r"(?i)\b(for\s+invalid\s+user|for\s+user|user|account|for)\s+([A-Za-z_][\w.\-]{1,63})"
)

SECRET_PLACEHOLDER = "<SECRET>"
BLOB_PLACEHOLDER = "<BLOB>"

# Words that follow "user"/"for" but are not identities.
_NOT_USERS = frozenset(
    {"invalid", "user", "password", "uid", "unknown", "root@", "authentication", "from"}
)


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Return ``(masked_text, mapping)``. Deterministic for identical input."""
    if not text:
        return text, {}

    mapping: dict[str, str] = {}

    # 1. Secrets first: a password that happens to look like a user must not become <USER_1>.
    def replace_secret(match: object) -> str:
        key = match.group(1)  # type: ignore[attr-defined]
        separator = match.group(2)  # type: ignore[attr-defined]
        value = match.group(3)  # type: ignore[attr-defined]
        mapping[str(value)] = SECRET_PLACEHOLDER
        return f"{key}{separator}{SECRET_PLACEHOLDER}"

    masked = _RE_SECRET_KV.sub(replace_secret, text)

    # 2. Emails, numbered per distinct address.
    masked = _number(masked, _RE_EMAIL, "EMAIL", mapping)

    # 3. Opaque blobs — no number, they are interchangeable noise.
    for pattern in (_RE_HEX_BLOB, _RE_B64_BLOB):
        for found in pattern.finditer(masked):
            value = found.group()
            if value in mapping or value.startswith("<"):
                continue
            mapping[value] = BLOB_PLACEHOLDER
        for value, placeholder in list(mapping.items()):
            if placeholder == BLOB_PLACEHOLDER and value in masked:
                masked = masked.replace(value, placeholder)

    # 4. Users: keyed form first (authoritative), then prose.
    users: dict[str, str] = {}

    def replace_user_kv(match: object) -> str:
        key = match.group(1)  # type: ignore[attr-defined]
        separator = match.group(2)  # type: ignore[attr-defined]
        value = str(match.group(3))  # type: ignore[attr-defined]
        if value.startswith("<"):
            return str(match.group(0))  # type: ignore[attr-defined]
        placeholder = users.setdefault(value, f"<USER_{len(users) + 1}>")
        mapping[value] = placeholder
        return f"{key}{separator}{placeholder}"

    masked = _RE_USER_KV.sub(replace_user_kv, masked)

    def replace_user_prose(match: object) -> str:
        lead = match.group(1)  # type: ignore[attr-defined]
        value = str(match.group(2))  # type: ignore[attr-defined]
        whole = str(match.group(0))  # type: ignore[attr-defined]
        if value.lower() in _NOT_USERS or value.startswith("<") or value.isdigit():
            return whole
        placeholder = users.setdefault(value, f"<USER_{len(users) + 1}>")
        mapping[value] = placeholder
        return f"{lead} {placeholder}"

    masked = _RE_USER_PROSE.sub(replace_user_prose, masked)

    return masked, mapping


def _number(text: str, pattern: object, label: str, mapping: dict[str, str]) -> str:
    """Replace every match with ``<LABEL_n>``, numbered by first appearance."""
    seen: dict[str, str] = {}
    for found in pattern.finditer(text):  # type: ignore[attr-defined]
        value = found.group()
        if value not in seen:
            seen[value] = f"<{label}_{len(seen) + 1}>"
    for value, placeholder in seen.items():
        text = text.replace(value, placeholder)
        mapping[value] = placeholder
    return text
