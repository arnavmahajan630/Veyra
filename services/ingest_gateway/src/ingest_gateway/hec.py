"""Parsing Splunk HEC bodies.

The quirk this module exists for: the HEC event endpoint accepts **concatenated JSON objects with no
separator at all** —

    {"event":"one"}{"event":"two"}
    {"event":"three"}

— which is not JSON, not NDJSON, and not something `json.loads` can read. Brace counting is the
obvious approach and the wrong one, because a brace inside a string (`{"event":"a } b"}`) breaks it.
So the split is done with `json.JSONDecoder().raw_decode`, which returns the index where each value
ended and handles strings, escapes and nesting correctly because it is the same parser as everything
else.

The raw bytes of an event are the `event` value itself when it is a string — so what the archive
holds is exactly the log line the sender had, not a JSON wrapper VEYRA added. When `event` is an
object, its compact serialization is used, with `framing.method="http_body"` recording that.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from veyra_common.models import HecMeta

# Keys HEC defines around the event itself; anything else in the object is the sender's business.
META_KEYS = ("time", "host", "source", "sourcetype", "index")


class HecFormatError(ValueError):
    """The body is not a sequence of HEC objects. Answered with 400, HEC-shaped."""


@dataclass(slots=True)
class HecEvent:
    """One event carved out of an HEC body."""

    raw: bytes
    meta: HecMeta | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def split_objects(body: str) -> list[dict[str, Any]]:
    """Split a body of concatenated JSON objects into the objects themselves.

    Whitespace (including newlines) between objects is allowed, which makes NDJSON a special case
    of the same rule — Vector's `splunk_hec_logs` sink sends newline-separated objects, other
    clients send them back to back, and both work.
    """
    decoder = json.JSONDecoder()
    index = 0
    length = len(body)
    objects: list[dict[str, Any]] = []
    while index < length:
        while index < length and body[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            value, end = decoder.raw_decode(body, index)
        except json.JSONDecodeError as exc:
            where = "at the start" if not objects else f"after {len(objects)} object(s)"
            raise HecFormatError(f"invalid JSON {where}: {exc.msg} (offset {exc.pos})") from exc
        if not isinstance(value, dict):
            raise HecFormatError(f"expected a JSON object, got {type(value).__name__}")
        objects.append(value)
        index = end
    if not objects:
        raise HecFormatError("empty body")
    return objects


def event_from_object(payload: dict[str, Any]) -> HecEvent:
    """Turn one HEC object into raw bytes plus the sender's metadata."""
    if "event" not in payload:
        raise HecFormatError("object has no 'event' field")
    event = payload["event"]
    if isinstance(event, str):
        raw = event.encode("utf-8")
    elif event is None:
        raise HecFormatError("'event' is null")
    else:
        # Compact and key-sorted, so the same object always produces the same bytes and therefore
        # the same raw_sha256 — two identical events must not look different to the vault.
        raw = json.dumps(event, separators=(",", ":"), sort_keys=True).encode("utf-8")

    claims = {key: payload[key] for key in META_KEYS if payload.get(key) is not None}
    if "time" in claims:
        try:
            claims["time"] = float(claims["time"])
        except (TypeError, ValueError):
            raise HecFormatError(f"'time' is not a number: {claims['time']!r}") from None
    meta = HecMeta.model_validate(claims) if claims else None
    extras = {k: v for k, v in payload.items() if k not in META_KEYS and k != "event"}
    return HecEvent(raw=raw, meta=meta, extras=extras)


def events_from_body(body: str) -> list[HecEvent]:
    """The whole HEC event endpoint's parsing, in one call."""
    return [event_from_object(obj) for obj in split_objects(body)]
