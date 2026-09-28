"""Token ids → an IF-CONTRACT-YAML template, and templates → contract YAML (C4 "Post-processing").

Walking the first sample's tokens in order:

- a token the response maps becomes a capture named for its path: the kv key when it has
  one (``user=<user>``), ``src_ip``/``dst_port`` for endpoints, else the path's last segment,
  with ``_2``… suffixes for uniqueness; typed ``:ip`` / ``:int``;
- a variable token not mapped becomes ``<*>``, except one right after ``word:`` or
  ``word=``, which becomes a named capture listed under ``unmapped`` (``attempts:<attempts:int>``);
- literal text is kept, with ``<``, ``>`` and ``\\`` escaped (TC3).

Every template maps ``message: $__text`` too. ``compile()`` is the round-trip check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import yaml

from veyra_contracts.drafting.classify import Layers
from veyra_contracts.drafting.request import Prepared
from veyra_contracts.drafting.schema import DraftResponse
from veyra_contracts.lint import PII_FIELDS
from veyra_engine import extract_tokens

TYPED = {"ip": ":ip", "ipv6": ":ip", "int": ":int", "port": ":int"}


@dataclass
class TemplateDraft:
    id: str
    pattern: str
    class_: str
    activity: str
    map: dict[str, Any]
    unmapped: list[str] = field(default_factory=list)

    def as_yaml_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "pattern": self.pattern,
            "class": self.class_,
            "activity": self.activity,
            "map": self.map,
        }
        if self.unmapped:
            out["unmapped"] = self.unmapped
        return out


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("<", "\\<").replace(">", "\\>")


def _common_prefix(texts: list[str]) -> str:
    prefix = texts[0]
    for text in texts[1:]:
        while not text.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def _divergent_tail(texts: list[str]) -> str:
    """A tail that differs across samples (`312s` vs `319s`) becomes `<*>`."""
    prefix = _common_prefix(texts)
    suffix = _common_prefix([text[::-1] for text in texts])[::-1]
    room = min(len(text) for text in texts) - len(prefix)
    if len(suffix) > room:
        suffix = suffix[len(suffix) - room :] if room else ""
    return _escape(prefix) + "<*>" + _escape(suffix)


def _tail(prepared: Prepared, cursor: int, consumed: int) -> str:
    """Literal text after the last capture. A span the samples disagree on becomes a wildcard.

    ``consumed`` is the index of the last token the pattern walked. Tokens after it
    (stable words such as ``after``) stay inside this tail, so they are not dropped
    when only the very end (``312s`` versus ``319s``) differs.
    """
    tail = prepared.text[cursor:]
    tails = [tail]
    for sample in prepared.samples[1:]:
        tokens = extract_tokens(sample.template_text)
        if [token.kind for token in tokens] != [token.kind for token in prepared.tokens]:
            return _escape(tail)
        start = tokens[consumed].end if consumed >= 0 else 0
        tails.append(sample.template_text[start:])
    if len(set(tails)) == 1:
        return _escape(tail)
    return _divergent_tail(tails)


def _capture_name(path: str, key: str | None) -> str:
    if key and key.isidentifier():
        return key.lower()
    parent, _, leaf = path.rpartition(".")
    if parent.endswith("_endpoint"):
        return f"{parent.removesuffix('_endpoint')}_{leaf}"
    return leaf


def _unique(name: str, taken: set[str]) -> str:
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name}_{n}", n + 1
    taken.add(candidate)
    return candidate


def template_id(response: DraftResponse, taken: set[str]) -> str:
    status = next((m.const for m in response.mappings if m.ocsf_path == "status_id"), None)
    suffix = {1: "_ok", 2: "_failed"}.get(status or 0, "")
    return _unique(f"{response.activity}{suffix}", taken)


def generalize(
    prepared: Prepared, response: DraftResponse, *, taken_ids: set[str] | None = None
) -> TemplateDraft:
    text = prepared.text
    by_token = {m.token: m.ocsf_path for m in response.mappings if m.token is not None}
    names: set[str] = {"__text"}
    parts: list[str] = []
    mapping: dict[str, Any] = {}
    unmapped: list[str] = []
    cursor = 0
    consumed = -1
    for index, token in enumerate(prepared.tokens):
        path = by_token.get(token.id)
        if path is None and token.id not in prepared.variable:
            continue
        literal = text[cursor : token.start]
        end = token.end
        if token.kind == "quoted" and literal.endswith('"') and text[end : end + 1] == '"':
            literal, end = literal[:-1], end + 1  # the quotes belong to the capture
        if path is not None:
            name = _unique(_capture_name(path, token.key), names)
            kind = ":quoted" if token.kind == "quoted" else TYPED.get(token.kind, "")
            parts.append(_escape(literal) + f"<{name}{kind}>")
            mapping[path] = f"${name}"
        elif literal[-1:] in (":", "=") and literal[:-1].split()[-1:]:
            word = literal[:-1].split()[-1]
            name = _unique(word.lower() if word.isidentifier() else "value", names)
            parts.append(_escape(literal) + f"<{name}{TYPED.get(token.kind, '')}>")
            unmapped.append(name)
        else:
            parts.append(_escape(literal) + "<*>")
        cursor = end
        consumed = index
    parts.append(_tail(prepared, cursor, consumed))
    for m in response.mappings:
        if m.const is not None:
            mapping[m.ocsf_path] = {"const": m.const}
    mapping.setdefault("message", "$__text")
    return TemplateDraft(
        id=template_id(response, set(taken_ids or ())),
        pattern="".join(parts),
        class_=response.class_,
        activity=response.activity,
        map=mapping,
        unmapped=unmapped,
    )


def _time_block(layers: Layers, timezone: str) -> dict[str, Any] | None:
    if any("syslog" in layer for layer in layers):
        return {"field": "syslog.timestamp", "timezone": timezone, "year": "infer_from_received"}
    return None


def _dump(document: dict[str, Any]) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=1000)


def new_contract(
    *,
    contract_id: str,
    tenant_id: str,
    source_id: str,
    layers: Layers,
    templates: list[TemplateDraft],
    timezone: str,
    drafted_by: str,
    draft_id: str,
    description: str = "",
) -> str:
    """Version 1 of a contract for a source onboarded from samples."""
    mapped = {path for t in templates for path in t.map}
    document: dict[str, Any] = {
        "contract": contract_id,
        "version": 1,
        "tenant": tenant_id,
        "sources": [source_id],
        "description": description or f"Drafted from samples of {source_id}",
        "envelope": layers,
    }
    time_block = _time_block(layers, timezone)
    if time_block:
        document["time"] = time_block
    document |= {
        "templates": [t.as_yaml_dict() for t in templates],
        "required": ["time"],
        "pii": sorted(mapped & PII_FIELDS),
        "provenance": {"drafted_by": drafted_by, "draft_id": draft_id},
    }
    return _dump(document)


def add_template(
    active_yaml: str, template: TemplateDraft, *, drafted_by: str, draft_id: str
) -> str:
    """The next version of an existing contract with one more template (drift)."""
    document: dict[str, Any] = yaml.safe_load(active_yaml)
    document["version"] = int(document["version"]) + 1
    document.pop("state", None)
    document["templates"] = [*document.get("templates", []), template.as_yaml_dict()]
    mapped = {path for t in document["templates"] for path in t.get("map", {})}
    document["pii"] = sorted(set(document.get("pii", [])) | (mapped & PII_FIELDS))
    document["provenance"] = {"drafted_by": drafted_by, "draft_id": draft_id}
    return _dump(document)


def existing_template_ids(contract_yaml: str) -> set[str]:
    document = yaml.safe_load(contract_yaml) or {}
    return {str(t.get("id")) for t in document.get("templates", [])}
