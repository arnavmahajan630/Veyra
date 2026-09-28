"""The drafting prompt (C4 "Prompt"): role, rules, schema, two compact examples."""

from __future__ import annotations

import hashlib
import json
from typing import Any

SYSTEM = """\
You map log fields to OCSF for a log pre-processor. You receive one log message shape as
JSON: masked samples, the variable tokens of the first sample (id, value, kind, key),
the allowed OCSF classes with their activities, the allowed field paths, and enums.
Rules:
- Only use token ids from "tokens". Never write a value yourself.
- A constant ("const") must be an integer from "enums" for that path.
- Pick one class and one activity from "allowed_classes".
- If unsure about a mapping, leave it out. A missing mapping is fine; a wrong one is not.
- Map each path at most once.
Answer with JSON only: {"class", "activity", "confidence": "high|medium|low",
"mappings": [{"ocsf_path", "token"} or {"ocsf_path", "const"}], "rationale"}."""

FEW_SHOT: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (
        {
            "samples_masked": ["Failed password for <USER_1> from 45.12.3.9 port 52144 ssh2"],
            "tokens": [
                {"id": "k4", "value": "<USER_1>", "kind": "word"},
                {"id": "k6", "value": "45.12.3.9", "kind": "ip"},
                {"id": "k8", "value": "52144", "kind": "int"},
            ],
        },
        {
            "class": "authentication",
            "activity": "logon",
            "confidence": "high",
            "mappings": [
                {"ocsf_path": "user.name", "token": "k4"},
                {"ocsf_path": "src_endpoint.ip", "token": "k6"},
                {"ocsf_path": "src_endpoint.port", "token": "k8"},
                {"ocsf_path": "status_id", "const": 2},
            ],
            "rationale": "sshd password failure from a client address and port",
        },
    ),
    (
        {
            "samples_masked": ["traffic deny src=45.12.3.9 dst=10.2.3.4 dpt=22"],
            "tokens": [
                {"id": "k3", "value": "45.12.3.9", "kind": "kv_value", "key": "src"},
                {"id": "k4", "value": "10.2.3.4", "kind": "kv_value", "key": "dst"},
                {"id": "k5", "value": "22", "kind": "kv_value", "key": "dpt"},
            ],
        },
        {
            "class": "network_activity",
            "activity": "refuse",
            "confidence": "high",
            "mappings": [
                {"ocsf_path": "src_endpoint.ip", "token": "k3"},
                {"ocsf_path": "dst_endpoint.ip", "token": "k4"},
                {"ocsf_path": "dst_endpoint.port", "token": "k5"},
                {"ocsf_path": "disposition_id", "const": 2},
            ],
            "rationale": "a firewall deny between two endpoints",
        },
    ),
]


def messages(request: dict[str, Any], *, complaint: str | None = None) -> list[dict[str, str]]:
    out = [{"role": "system", "content": SYSTEM}]
    for example_in, example_out in FEW_SHOT:
        out.append({"role": "user", "content": json.dumps(example_in)})
        out.append({"role": "assistant", "content": json.dumps(example_out)})
    out.append({"role": "user", "content": json.dumps(request, ensure_ascii=False)})
    if complaint:
        out.append(
            {"role": "user", "content": f"Your answer was rejected: {complaint}. Try again."}
        )
    return out


def prompt_sha(chat: list[dict[str, str]]) -> str:
    return hashlib.sha256(json.dumps(chat, sort_keys=True).encode("utf-8")).hexdigest()
