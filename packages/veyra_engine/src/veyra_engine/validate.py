"""Validate a normalized event against the vendored OCSF subset.

Two validators, on purpose:

* **hot path** — ``fastjsonschema`` compiles each class's schema into Python once and then checks
  an event in ~12 us. A3's risk list said "jsonschema is slow; cache validators and **measure**";
  measuring said plain ``jsonschema`` cost ~216 us per event, about a third of the whole
  pipeline, so the compiled validator is what runs per event.
* **diagnostics** — when an event really is invalid (rare, and off the hot path by definition),
  ``jsonschema`` lists every problem for the DLQ reason, which is far more useful to whoever is
  fixing the contract than "first error wins".

Both read the same vendored schema file, so what runs is what an auditor can inspect.

A validation failure is never an exception. It downgrades the event's tier and lands in the DLQ
with a reason (P2), because a schema-invalid event still has to reach the SIEM.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import fastjsonschema
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parent / "ocsf" / "subset_1.9.0.json"


@dataclass(slots=True)
class ValidationResult:
    """Outcome of validating one event."""

    ok: bool
    errors: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        parts = []
        if self.missing_required:
            parts.append("missing " + ", ".join(self.missing_required))
        if self.errors:
            parts.append("; ".join(self.errors[:3]))
        return " | ".join(parts)


@lru_cache(maxsize=1)
def _schema_document() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def ocsf_version() -> str:
    """The pinned OCSF version these schemas describe (IF-VERSIONS)."""
    return str(_schema_document()["ocsf_version"])


def supported_classes() -> tuple[int, ...]:
    return tuple(sorted(int(uid) for uid in _schema_document()["classes"]))


def _schema_for(class_uid: int) -> dict[str, Any]:
    """A class's schema, falling back to Base Event for anything unknown."""
    classes = _schema_document()["classes"]
    return classes.get(str(class_uid)) or classes["0"]


@lru_cache(maxsize=16)
def fast_validator_for(class_uid: int) -> Callable[[dict[str, Any]], Any]:
    """Compiled hot-path validator. Raises ``JsonSchemaException`` on the first problem."""
    return fastjsonschema.compile(_schema_for(class_uid))


@lru_cache(maxsize=16)
def validator_for(class_uid: int) -> Draft202012Validator:
    """Full validator, used to *explain* a failure rather than to detect one."""
    return Draft202012Validator(_schema_for(class_uid))


def validate_event(
    event: dict[str, Any], required_paths: list[str] | None = None
) -> ValidationResult:
    """Schema-check an event and confirm the contract's ``required:`` paths are present."""
    class_uid = event.get("class_uid", 0)
    try:
        class_uid = int(class_uid)
    except (TypeError, ValueError):
        class_uid = 0

    errors: list[str] = []
    try:
        fast_validator_for(class_uid)(event)
    except fastjsonschema.JsonSchemaException:
        # Invalid, so now spend the time to say exactly how.
        for error in sorted(validator_for(class_uid).iter_errors(event), key=str):
            location = ".".join(str(part) for part in error.absolute_path) or "(root)"
            errors.append(f"{location}: {error.message}")
        if not errors:  # pragma: no cover - the two validators disagreeing is a bug
            errors.append("schema validation failed")

    missing: list[str] = []
    for path in required_paths or ():
        if not _has_path(event, path):
            missing.append(path)

    return ValidationResult(ok=not errors and not missing, errors=errors, missing_required=missing)


def _has_path(event: dict[str, Any], path: str) -> bool:
    """Is a dotted path present and non-empty? ``time`` counts even when it is 0."""
    cursor: Any = event
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return False
        cursor = cursor[part]
    return cursor is not None and cursor != ""
