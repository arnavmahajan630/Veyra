"""``mini_compile`` — a small contract compiler, for tests and for the pre-C2 world.

C2 owns the real compiler (`veyra_contracts.compile`). Until it exists, A needs *something* that
turns IF-CONTRACT-YAML into IF-CONTRACT-COMPILED so the engine, the golden tests and
`tools/mock_control_publish.py` can run. This is that something, and it is deliberately named
``testing`` so nobody mistakes it for the production path.

It implements exactly the YAML forms the plan documents — no extensions, so a contract that
works here works with C2's compiler too. When C2 lands, this module stays only as a test helper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yaml

from veyra_engine.template import compile_pattern

COMPILER_VERSION = "mini-0.1.0"

# OCSF class names as contracts write them -> class_uid (verified against OCSF 1.9.0).
CLASS_UIDS: dict[str, int] = {
    "base_event": 0,
    "process_activity": 1007,
    "authentication": 3002,
    "network_activity": 4001,
    "http_activity": 4002,
}

# Activity names per class -> activity_id (IF-OCSF-SUBSET's "Activities used").
ACTIVITY_IDS: dict[int, dict[str, int]] = {
    0: {"unknown": 0, "other": 99},
    1007: {"launch": 1, "terminate": 2, "open": 3, "inject": 4, "other": 99},
    3002: {
        "logon": 1,
        "logoff": 2,
        "authentication_ticket": 3,
        "service_ticket_request": 4,
        "preauth": 6,
        "other": 99,
    },
    4001: {
        "open": 1,
        "close": 2,
        "reset": 3,
        "fail": 4,
        "refuse": 5,
        "traffic": 6,
        "listen": 7,
        "other": 99,
    },
    4002: {"connect": 1, "delete": 2, "get": 3, "head": 4, "post": 6, "put": 7, "other": 99},
}

# class_uid -> the norm.<category> topic segment (IF-TOPICS).
CLASS_CATEGORY: dict[int, str] = {
    0: "uncategorized",
    1007: "system",
    3002: "iam",
    4001: "network",
    4002: "network",
}


class CompileError(ValueError):
    """The contract is not valid. Raised at compile time, never at runtime."""


def compile_map_entry(ocsf_path: str, spec: Any) -> dict[str, Any]:
    """Translate one ``map:`` value into its compiled form (IF-CONTRACT-COMPILED)."""
    if isinstance(spec, str):
        if spec == "$__text":
            return {"ocsf_path": ocsf_path, "kind": "text"}
        if not spec.startswith("$"):
            raise CompileError(f"{ocsf_path}: a plain string must start with '$', got {spec!r}")
        ref = spec[1:]
        # A dotted reference is a peeled field (json.msg, syslog.host, cef.src); a bare name is
        # a template capture.
        kind = "field" if "." in ref else "capture"
        return {"ocsf_path": ocsf_path, "kind": kind, "ref": ref}

    if isinstance(spec, dict):
        if "const" in spec:
            return {"ocsf_path": ocsf_path, "kind": "const", "value": spec["const"]}
        if "vocab" in spec:
            source = str(spec.get("from", "")).lstrip("$")
            if not source:
                raise CompileError(f"{ocsf_path}: vocab needs a 'from'")
            return {
                "ocsf_path": ocsf_path,
                "kind": "vocab",
                "vocab": spec["vocab"],
                "ref": source,
            }
        if "ts" in spec:
            return {
                "ocsf_path": ocsf_path,
                "kind": "ts",
                "ref": str(spec["ts"]).lstrip("$"),
                "formats": spec.get("formats"),
            }
        raise CompileError(f"{ocsf_path}: unsupported map form {sorted(spec)}")

    raise CompileError(f"{ocsf_path}: unsupported map value {spec!r}")


def mini_compile(yaml_text: str) -> dict[str, Any]:
    """Compile IF-CONTRACT-YAML text into the dict the engine consumes."""
    try:
        document = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise CompileError(f"not valid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise CompileError("a contract must be a YAML mapping")

    contract_id = document.get("contract")
    if not contract_id:
        raise CompileError("missing 'contract'")

    templates: list[dict[str, Any]] = []
    for spec in document.get("templates") or []:
        template_id = spec.get("id")
        if not template_id:
            raise CompileError(f"{contract_id}: a template has no id")
        pattern = spec.get("pattern")
        if pattern is None:
            raise CompileError(f"{contract_id}/{template_id}: no pattern")

        regex, captures = compile_pattern(str(pattern))

        class_name = str(spec.get("class", "base_event"))
        if class_name not in CLASS_UIDS:
            raise CompileError(f"{contract_id}/{template_id}: unknown class {class_name!r}")
        class_uid = CLASS_UIDS[class_name]

        activity_name = str(spec.get("activity", "unknown")).lower()
        activities = ACTIVITY_IDS.get(class_uid, {})
        if activity_name not in activities:
            raise CompileError(
                f"{contract_id}/{template_id}: activity {activity_name!r} is not valid for "
                f"{class_name} (allowed: {sorted(activities)})"
            )
        activity_id = activities[activity_name]

        entries = [
            compile_map_entry(path, value) for path, value in (spec.get("map") or {}).items()
        ]

        templates.append(
            {
                "id": str(template_id),
                "regex": regex,
                "captures": [{"name": c.name, "type": c.type} for c in captures],
                "class_uid": class_uid,
                "activity_id": activity_id,
                # IF-OCSF-SUBSET: type_uid = class_uid * 100 + activity_id.
                "type_uid": class_uid * 100 + activity_id,
                "category": CLASS_CATEGORY.get(class_uid, "uncategorized"),
                "map": entries,
                "unmapped": list(spec.get("unmapped") or []),
            }
        )

    return {
        "contract": str(contract_id),
        "version": int(document.get("version", 1)),
        "tenant": document.get("tenant"),
        "sources": list(document.get("sources") or []),
        "envelope": list(document.get("envelope") or []),
        "time": dict(document.get("time") or {}),
        "templates": templates,
        "required": list(document.get("required") or []),
        "enrich": list(document.get("enrich") or []),
        "pii": list(document.get("pii") or []),
        "vocab": list(document.get("vocab") or []),
        "state": document.get("state", "active"),
        "compiled_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "compiler_version": COMPILER_VERSION,
    }


def mini_compile_file(path: Any) -> dict[str, Any]:
    """Compile a contract from disk."""
    from pathlib import Path

    return mini_compile(Path(path).read_text())
