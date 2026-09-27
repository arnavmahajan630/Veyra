"""PII masking for DLQ text and LLM prompts.

A3 ships the version its phase file specifies: values of secret-like keys, and email
addresses. A4 replaces it with the deterministic full version (``<USER_n>``, ``<EMAIL_n>``,
``<SECRET>``, ``<BLOB>``) — the signature here will not change.

**IPs are deliberately kept.** They are not secrets, and the drafter and the drift review both
need them to propose a mapping (A4, IF-LLM-DRAFT). Masking them would make the whole loop
useless.
"""

from __future__ import annotations

import re2

# Keys whose value is a credential. Matched case-insensitively on the key name only.
_SECRET_KEY = (
    r"(?i)\b(pass|passwd|password|pwd|token|secret|apikey|api_key|key|session|cookie|auth)\b"
)
_RE_SECRET_KV = re2.compile(_SECRET_KEY + r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_RE_EMAIL = re2.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

SECRET_PLACEHOLDER = "<SECRET>"


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Return ``(masked_text, mapping)``; the mapping records what was replaced.

    Deterministic: the same input always produces the same output, which matters because the
    DLQ text feeds Drain3 clustering (C3) and the LLM cache key (C4).
    """
    mapping: dict[str, str] = {}

    def secret(match: object) -> str:
        key = match.group(1)  # type: ignore[attr-defined]
        separator = match.group(2)  # type: ignore[attr-defined]
        value = match.group(3)  # type: ignore[attr-defined]
        mapping[str(value)] = SECRET_PLACEHOLDER
        return f"{key}{separator}{SECRET_PLACEHOLDER}"

    masked = _RE_SECRET_KV.sub(secret, text)

    emails: dict[str, str] = {}
    for found in _RE_EMAIL.finditer(masked):
        value = found.group()
        if value not in emails:
            emails[value] = f"<EMAIL_{len(emails) + 1}>"
    for value, placeholder in emails.items():
        masked = masked.replace(value, placeholder)
        mapping[value] = placeholder

    return masked, mapping
