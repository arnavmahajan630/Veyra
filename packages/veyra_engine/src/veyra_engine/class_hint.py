"""Guess what a tier-3 event probably *is*, without ever pretending to know.

At tier 3 no contract matched, so VEYRA has no authority to set ``class_uid``: doing so would let a
guess reach Wazuh's rules as if it were a mapping, which is the kind of quiet lie this design
refuses. The guess rides in ``ulpf.class_hint`` with a confidence instead, where the console can
show it and the drafter (C4) can use it as a start. ``class_uid`` stays 0 (Base Event).

Rules are A4's table, in order, and deliberately dull: keywords plus the token kinds already
extracted. No scoring model, because it has to be deterministic and explainable.
"""

from __future__ import annotations

from dataclasses import dataclass

import re2

from veyra_engine.types import Token

# Keyword sets per class. Matched case-insensitively against the whole text.
_RE_AUTH = re2.compile(
    r"(?i)\b(login|logon|logoff|auth|authentication|password|ssh|sudo|su|kerberos)\b"
)
_RE_NETWORK = re2.compile(
    r"(?i)\b(allow|allowed|deny|denied|drop|dropped|accept|accepted"
    r"|block|blocked|traffic|firewall)\b"
)
_RE_HTTP = re2.compile(r"(?i)(\bGET\b|\bPOST\b|\bPUT\b|\bDELETE\b|\bHEAD\b|HTTP/\d)")
_RE_PROCESS = re2.compile(r"(?i)\b(exec|execve|spawn|pid|process|command|cmdline)\b")

CLASS_AUTHENTICATION = 3002
CLASS_NETWORK_ACTIVITY = 4001
CLASS_HTTP_ACTIVITY = 4002
CLASS_PROCESS_ACTIVITY = 1007


@dataclass(frozen=True, slots=True)
class ClassHintResult:
    """A guess plus how much to trust it."""

    class_uid: int
    confidence: str  # "low" | "medium" | "high"

    def as_dict(self) -> dict[str, object]:
        return {"class_uid": self.class_uid, "confidence": self.confidence}


def class_hint(text: str, tokens: list[Token]) -> ClassHintResult | None:
    """The first rule that fires wins, mirroring A4's table. ``None`` means no guess."""
    kinds = {token.kind for token in tokens}
    ip_count = sum(1 for token in tokens if token.kind in ("ip", "ipv6"))
    has_user = "user" in kinds
    has_port = "port" in kinds

    # HTTP before network: an HTTP line usually also says "GET ... 200" and has IPs, and the more
    # specific class is the more useful hint.
    if _RE_HTTP.search(text):
        return ClassHintResult(CLASS_HTTP_ACTIVITY, "medium" if ip_count else "low")

    if _RE_AUTH.search(text):
        # A user token is what makes an auth guess worth acting on.
        return ClassHintResult(CLASS_AUTHENTICATION, "medium" if has_user else "low")

    if _RE_NETWORK.search(text) and ip_count >= 2 and has_port:
        return ClassHintResult(CLASS_NETWORK_ACTIVITY, "medium")
    if _RE_NETWORK.search(text) and ip_count >= 2:
        return ClassHintResult(CLASS_NETWORK_ACTIVITY, "low")

    if _RE_PROCESS.search(text):
        return ClassHintResult(CLASS_PROCESS_ACTIVITY, "low")

    return None


# Severity words, A4's list. A match sets severity_id 3 (Medium) and is recorded in
# derived_fields as "vocab:severity_words" — a derived value, never an offset.
_RE_SEVERITY_BAD = re2.compile(
    r"(?i)\b(fail|failed|failure|error|denied|deny|blocked|critical|alert|panic|fatal|refused)\b"
)
_RE_SEVERITY_WORST = re2.compile(r"(?i)\b(critical|alert|panic|fatal)\b")

SEVERITY_UNKNOWN = 0
SEVERITY_INFORMATIONAL = 1
SEVERITY_MEDIUM = 3
SEVERITY_HIGH = 4


def severity_from_words(text: str) -> int | None:
    """Severity from the word list, or ``None`` when nothing matches (stays Unknown)."""
    if _RE_SEVERITY_WORST.search(text):
        return SEVERITY_HIGH
    if _RE_SEVERITY_BAD.search(text):
        return SEVERITY_MEDIUM
    return None
