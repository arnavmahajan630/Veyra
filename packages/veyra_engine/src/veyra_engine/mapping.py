"""Turn captures and peeled fields into OCSF, recording where every value came from.

This is where P4 is either honoured or quietly broken, so the rule is explicit: a value that
is literally present in the raw event gets a **byte span** in ``ulpf.field_offsets``; anything
computed — a constant, a vocabulary lookup, a parsed timestamp, an enrichment — gets an entry
in ``ulpf.derived_fields`` instead. Nothing is allowed to be in neither.

Consumes the compiled map form from IF-CONTRACT-COMPILED::

    {"ocsf_path": "user.name", "kind": "capture", "ref": "user"}
    {"ocsf_path": "status_id",  "kind": "const",   "value": 2}
    {"ocsf_path": "severity_id","kind": "vocab",   "vocab": "status_words", "ref": "status"}
    {"ocsf_path": "time",       "kind": "ts",      "ref": "ts", "formats": [...]}
    {"ocsf_path": "message",    "kind": "text"}
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

from veyra_engine.decode import Decoded
from veyra_engine.peel import PeeledField

# OCSF observable type ids used by the subset (IF-OCSF-SUBSET).
OBSERVABLE_TYPE_HOSTNAME = 1
OBSERVABLE_TYPE_IP = 2
OBSERVABLE_TYPE_USER = 4

# Which OCSF paths are IPs, users and hostnames — drives observables and typing.
IP_PATHS = frozenset(
    {"src_endpoint.ip", "dst_endpoint.ip", "device.ip", "src_endpoint.intermediate_ips"}
)
PORT_PATHS = frozenset({"src_endpoint.port", "dst_endpoint.port"})
USER_PATHS = frozenset({"user.name", "actor.user.name", "user.uid", "user.domain"})
HOSTNAME_PATHS = frozenset({"src_endpoint.hostname", "dst_endpoint.hostname", "device.hostname"})
INT_PATHS = (
    frozenset(
        {
            "severity_id",
            "status_id",
            "disposition_id",
            "action_id",
            "activity_id",
            "class_uid",
            "category_uid",
            "type_uid",
            "http_response.code",
            "process.pid",
            "traffic.bytes_in",
            "traffic.bytes_out",
        }
    )
    | PORT_PATHS
)


@dataclass(slots=True)
class MappingResult:
    """The mapped OCSF fragment plus its provenance."""

    ocsf: dict[str, Any] = field(default_factory=dict)
    field_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    derived_fields: dict[str, str] = field(default_factory=dict)
    observables: list[dict[str, Any]] = field(default_factory=list)
    unmapped: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    coercion_errors: list[str] = field(default_factory=list)


def set_path(target: dict[str, Any], path: str, value: Any) -> None:
    """Write ``a.b.c`` into nested dicts, creating the intermediate objects."""
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def coerce(path: str, value: Any) -> tuple[Any, str | None]:
    """Type a value for its OCSF path. Returns ``(value, error)``; never raises."""
    if value is None:
        return None, None
    if path in IP_PATHS:
        try:
            return str(ipaddress.ip_address(str(value).strip())), None
        except ValueError:
            return None, f"{path}: {value!r} is not an IP address"
    if path in INT_PATHS:
        try:
            return int(str(value).strip()), None
        except (TypeError, ValueError):
            return None, f"{path}: {value!r} is not an integer"
    if isinstance(value, (dict, list)):
        return value, None
    return str(value), None


def apply_map(
    entries: list[dict[str, Any]],
    *,
    captures: dict[str, str],
    capture_spans: dict[str, tuple[int, int]],
    peeled: dict[str, PeeledField],
    text: str,
    text_span: tuple[int, int] | None,
    decoded: Decoded,
    vocab: dict[str, dict[str, Any]] | None = None,
    time_resolver: Any = None,
) -> MappingResult:
    """Apply a compiled contract's map entries.

    ``time_resolver`` is injected (rather than imported) so mapping stays pure and testable:
    it takes ``(value, formats)`` and returns epoch milliseconds plus a detail string.
    """
    result = MappingResult()
    vocab = vocab or {}
    consumed_captures: set[str] = set()
    consumed_fields: set[str] = set()

    for entry in entries:
        path = entry.get("ocsf_path")
        if not path:
            continue
        kind = entry.get("kind", "capture")

        if kind == "capture":
            ref = str(entry.get("ref", ""))
            if ref not in captures:
                result.missing.append(path)
                continue
            value, error = coerce(path, captures[ref])
            if error:
                result.coercion_errors.append(error)
                continue
            set_path(result.ocsf, path, value)
            consumed_captures.add(ref)
            span = decoded.byte_span(capture_spans.get(ref))
            if span is not None:
                result.field_offsets[path] = span
            else:
                result.derived_fields[path] = "capture:no-span"

        elif kind == "field":
            ref = str(entry.get("ref", ""))
            source = peeled.get(ref)
            if source is None:
                result.missing.append(path)
                continue
            value, error = coerce(path, source.value)
            if error:
                result.coercion_errors.append(error)
                continue
            set_path(result.ocsf, path, value)
            consumed_fields.add(ref)
            span = decoded.byte_span(source.char_span)
            if span is not None:
                result.field_offsets[path] = span
            else:
                result.derived_fields[path] = f"field:{source.layer}"

        elif kind == "const":
            value, error = coerce(path, entry.get("value"))
            if error:
                result.coercion_errors.append(error)
                continue
            set_path(result.ocsf, path, value)
            result.derived_fields[path] = "const"

        elif kind == "vocab":
            name = str(entry.get("vocab", ""))
            ref = str(entry.get("ref", ""))
            lookup_value = captures.get(ref)
            if lookup_value is None:
                source = peeled.get(ref)
                lookup_value = None if source is None else str(source.value)
            table = vocab.get(name, {})
            entries_map = table.get("entries", table) if isinstance(table, dict) else {}
            hit = entries_map.get(str(lookup_value).upper()) if lookup_value is not None else None
            if hit is None:
                result.missing.append(path)
                continue
            # A vocab row is {"status_id": 2}; take the value for this path's leaf, else the
            # whole row if it is scalar.
            leaf = path.split(".")[-1]
            value = hit.get(leaf) if isinstance(hit, dict) else hit
            if value is None:
                result.missing.append(path)
                continue
            typed, error = coerce(path, value)
            if error:
                result.coercion_errors.append(error)
                continue
            set_path(result.ocsf, path, typed)
            result.derived_fields[path] = f"vocab:{name}"
            if ref:
                consumed_captures.add(ref)

        elif kind == "ts":
            ref = str(entry.get("ref", ""))
            raw_value = captures.get(ref)
            if raw_value is None:
                source = peeled.get(ref)
                raw_value = None if source is None else str(source.value)
            if raw_value is None or time_resolver is None:
                result.missing.append(path)
                continue
            epoch_ms, detail = time_resolver(raw_value, entry.get("formats"))
            if epoch_ms is None:
                result.missing.append(path)
                continue
            set_path(result.ocsf, path, int(epoch_ms))
            result.derived_fields[path] = f"ts:{detail}"
            if ref:
                consumed_captures.add(ref)

        elif kind == "text":
            set_path(result.ocsf, path, text)
            span = decoded.byte_span(text_span)
            if span is not None:
                result.field_offsets[path] = span
            else:
                result.derived_fields[path] = "text:no-span"

        else:
            result.coercion_errors.append(f"{path}: unknown map kind {kind!r}")

    result.observables = build_observables(result.ocsf)
    result.unmapped = collect_unmapped(
        captures=captures,
        consumed_captures=consumed_captures,
        peeled=peeled,
        consumed_fields=consumed_fields,
    )
    return result


def build_observables(ocsf: dict[str, Any]) -> list[dict[str, Any]]:
    """Observables from the typed fields we mapped (IF-OCSF-SUBSET)."""
    observables: list[dict[str, Any]] = []
    ip_index = 0
    host_index = 0

    def get(path: str) -> Any:
        cursor: Any = ocsf
        for part in path.split("."):
            if not isinstance(cursor, dict):
                return None
            cursor = cursor.get(part)
        return cursor

    for path in sorted(IP_PATHS):
        value = get(path)
        if value:
            ip_index += 1
            observables.append(
                {"name": f"ip_{ip_index}", "type_id": OBSERVABLE_TYPE_IP, "value": str(value)}
            )
    for path in sorted(USER_PATHS):
        value = get(path)
        if value:
            observables.append(
                {"name": "user", "type_id": OBSERVABLE_TYPE_USER, "value": str(value)}
            )
            break
    for path in sorted(HOSTNAME_PATHS):
        value = get(path)
        if value:
            host_index += 1
            observables.append(
                {
                    "name": f"host_{host_index}",
                    "type_id": OBSERVABLE_TYPE_HOSTNAME,
                    "value": str(value),
                }
            )
    return observables


def collect_unmapped(
    *,
    captures: dict[str, str],
    consumed_captures: set[str],
    peeled: dict[str, PeeledField],
    consumed_fields: set[str],
) -> dict[str, Any]:
    """Everything parsed but not mapped. v1 requirement C: never lose data."""
    unmapped: dict[str, Any] = {}
    for name, value in captures.items():
        if name not in consumed_captures:
            unmapped[name] = value
    for path, peeled_field in peeled.items():
        if path in consumed_fields:
            continue
        # The JSON remainder and syslog scaffolding are still data; keep them under their
        # peeled path so nothing silently disappears.
        unmapped[path] = peeled_field.value
    return unmapped
