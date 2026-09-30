"""Per-route PII masking (IF-ROUTES ``masking``).

Two modes, and the difference between them is the whole point:

* ``hmac`` — HMAC-SHA256 under the **route's** key, truncated to 16 hex and prefixed ``h_``. It is
  deterministic, so a partner can correlate "the same user again" across events and across days
  without ever learning who it is, and it is keyed, so they cannot recover the value by hashing
  guesses (a plain SHA-256 of a username is trivially reversible with a wordlist).
* ``redact`` — replaced with ``[REDACTED]``. Used for `raw_data`, where there is nothing worth
  correlating on and the original bytes are the thing being withheld.

Masking never removes the key, only the value: a downstream schema check must still see the field.

**Masking follows the value, not just the path.** Masking `user.name` alone is theatre: the same
identity sits in `message`, in `observables[].value` and inside `raw_data`. A6's integration test
caught exactly that — one partner line had `user.name: h_…` next to
`message: "user=a.sharma FAILED…"`. So once a path's value is masked, every occurrence of that value
anywhere in the payload becomes the same placeholder, which also keeps the feed consistent: the
`h_…` in `message` is the same `h_…` as in `user.name`.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

REDACTED = "[REDACTED]"
HMAC_PREFIX = "h_"
HMAC_HEX = 16
MODES = ("hmac", "redact")
# Below this length a value is not swept through the rest of the payload: replacing every "ab" in a
# log line would mangle text that has nothing to do with the identity being protected. The declared
# path is still masked — only the sweep is skipped.
MIN_SWEEP_LENGTH = 4


@dataclass(frozen=True, slots=True)
class MaskingPlan:
    """Which OCSF paths a route masks, and how."""

    paths: tuple[tuple[str, str], ...] = ()

    @classmethod
    def parse(cls, spec: dict[str, Any] | str) -> MaskingPlan:
        if spec == "none" or not spec:
            return cls()
        if not isinstance(spec, dict):
            raise ValueError(f"masking must be a mapping or 'none', got {type(spec).__name__}")
        plan: list[tuple[str, str]] = []
        for path, mode in spec.items():
            if mode not in MODES:
                raise ValueError(f"unknown masking mode {mode!r} for {path}; known: {MODES}")
            plan.append((str(path), str(mode)))
        return cls(paths=tuple(sorted(plan)))

    def apply(self, event: dict[str, Any], key: bytes) -> dict[str, Any]:
        """A copy of ``event`` with each configured path masked; absent paths are skipped."""
        if not self.paths:
            return event
        masked = _deepcopy(event)
        # Pass one: the declared paths.
        replacements: dict[str, str] = {}
        for path, mode in self.paths:
            value = _dig(masked, path)
            if value is None:
                continue
            if mode == "redact":
                _put(masked, path, REDACTED)
                continue
            placeholder = hmac_value(str(value), key)
            _put(masked, path, placeholder)
            if len(str(value)) >= MIN_SWEEP_LENGTH:
                replacements[str(value)] = placeholder
        # Pass two: the same value wherever else it appears — message, observables, any text.
        # Short values are skipped: sweeping a 2-character name would mangle unrelated text.
        if replacements:
            _sweep(masked, replacements)
        return masked


def _sweep(node: Any, replacements: dict[str, str]) -> None:
    """Replace every occurrence of a masked value, in place, through the whole payload."""
    if isinstance(node, dict):
        for name, value in node.items():
            if isinstance(value, str):
                node[name] = _replace_all(value, replacements)
            else:
                _sweep(value, replacements)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                node[index] = _replace_all(value, replacements)
            else:
                _sweep(value, replacements)


def _replace_all(text: str, replacements: dict[str, str]) -> str:
    for original, placeholder in replacements.items():
        if original in text:
            text = text.replace(original, placeholder)
    return text


def hmac_value(value: str, key: bytes) -> str:
    digest = hmac.new(key, value.encode("utf-8"), sha256).hexdigest()
    return f"{HMAC_PREFIX}{digest[:HMAC_HEX]}"


def _deepcopy(value: Any) -> Any:
    """Copy just the containers, so masking cannot reach back into the delivered event."""
    if isinstance(value, dict):
        return {key: _deepcopy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deepcopy(item) for item in value]
    return value


def _dig(event: dict[str, Any], path: str) -> Any:
    cursor: Any = event
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def _put(event: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: Any = event
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    if isinstance(cursor, dict) and parts[-1] in cursor:
        cursor[parts[-1]] = value
