"""Tier 3: make an unrecognised log useful without inventing anything.

This is the phase of the demo a judge actually remembers (Beat 3): a source nobody wrote a contract
for still arrives in Wazuh with its IPs, users and key/values extracted, every one of them pointing
at real bytes, labelled honestly as ``unknown_template``.

What tier 3 must *not* do is as important as what it does:

* it never sets ``class_uid`` — the guess lives in ``ulpf.class_hint`` (see :mod:`class_hint`);
* it never assigns an IP to ``src_endpoint`` or ``dst_endpoint``, because at tier 3 nothing tells us
  which end is which, and guessing is what makes a SIEM rule fire on the wrong host;
* it never fabricates a byte range: anything not literally in the raw goes to ``derived_fields``.

The classifier cascade below is deterministic and in A4's order, so the same bytes always peel the
same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from veyra_engine.class_hint import class_hint, severity_from_words
from veyra_engine.decode import Decoded
from veyra_engine.peel import (
    PeeledField,
    Region,
    detect_cef,
    detect_csv,
    detect_kv,
    detect_leef,
    detect_syslog,
    peel_cef,
    peel_csv,
    peel_json,
    peel_kv,
    peel_leef,
    peel_syslog,
)
from veyra_engine.spanjson import looks_like_json
from veyra_engine.timeparse import TimeResult, parse_time
from veyra_engine.tokens import INTERESTING, extract_tokens
from veyra_engine.types import Budget, Token

# How much of a very long line the cascade looks at. Same reasoning and same value as
# `tokens.TOKENIZE_MAX_CHARS`: bounded work, and nothing lost from `raw_data`.
CASCADE_MAX_CHARS = 4096

# Kinds that become observables, mapped to OCSF observable type ids (IF-OCSF-SUBSET).
OBSERVABLE_TYPES: dict[str, int] = {
    "hostname": 1,
    "ip": 2,
    "ipv6": 2,
    "user": 4,
    "email": 5,
    "url": 6,
    "hash": 8,
    "uuid": 10,
}


@dataclass(slots=True)
class Tier3Result:
    """Everything tier 3 extracted, ready for the event builder."""

    text: str
    text_span: tuple[int, int]
    parse_path: list[str] = field(default_factory=list)
    fields: list[PeeledField] = field(default_factory=list)
    tokens: list[Token] = field(default_factory=list)
    observables: list[dict[str, Any]] = field(default_factory=list)
    unmapped: dict[str, Any] = field(default_factory=dict)
    field_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    derived_fields: dict[str, str] = field(default_factory=dict)
    class_hint: dict[str, Any] | None = None
    severity_id: int = 0
    time: TimeResult | None = None


def cascade(decoded: Decoded, *, max_depth: int = 4) -> tuple[list[str], list[PeeledField], Region]:
    """Auto-detect the layers of an unrecognised line (A4's order).

    Returns ``(parse_path, fields, text_region)``. The order is: syslog header, then a JSON body,
    then CEF, then LEEF, then key=value density, then a consistent delimiter, else plain text. Each
    step is recorded in ``parse_path`` as ``auto:<layer>`` so the console can show how a line was
    read.
    """
    parse_path: list[str] = []
    fields: list[PeeledField] = []
    # Peeling a 60 KB line costs ~8 ms per layer, over the whole per-event budget, and a message
    # that long is noise past the first few KB. The window matches the tokenizer's, and `raw_data`
    # still carries every byte.
    text = decoded.text[:CASCADE_MAX_CHARS]
    region = Region(text=text, span=(0, len(text)))
    depth = 0

    variant = detect_syslog(region)
    if variant:
        result = peel_syslog(region, "auto")
        if not result.error:
            parse_path.append(f"auto:{variant}")
            fields.extend(result.fields)
            depth += 1
            if result.body is not None:
                region = result.body

    if depth < max_depth and looks_like_json(region.text):
        result = peel_json(region)
        if not result.error:
            parse_path.append("auto:json")
            fields.extend(result.fields)
            depth += 1
            # The JSON remainder is where messy sources hide half their data (the demo's
            # `} | trace=` shape), so kv-parse it rather than leaving it as one blob.
            rest = next((f for f in result.fields if f.path == "json.__rest"), None)
            if rest is not None and isinstance(rest.value, str) and rest.char_span:
                rest_region = Region(text=rest.value, span=rest.char_span)
                if detect_kv(rest_region, min_pairs=1, density=0.1):
                    rest_kv = peel_kv(rest_region, prefix="rest")
                    if not rest_kv.error:
                        parse_path.append("auto:kv(rest)")
                        fields.extend(rest_kv.fields)
            # Templates would match the biggest string value; for tier 3 the useful text is the
            # longest string field, which is usually the human message.
            longest = _longest_string_field(result.fields)
            if longest is not None and longest.char_span:
                region = Region(text=str(longest.value), span=longest.char_span)

    if depth < max_depth and detect_cef(region):
        result = peel_cef(region)
        if not result.error:
            parse_path.append("auto:cef")
            fields.extend(result.fields)
            depth += 1
            if result.text_field is not None:
                region = result.text_field

    if depth < max_depth and detect_leef(region):
        result = peel_leef(region)
        if not result.error:
            parse_path.append("auto:leef")
            fields.extend(result.fields)
            depth += 1

    if depth < max_depth and detect_kv(region, min_pairs=2, density=0.5):
        result = peel_kv(region)
        if not result.error:
            parse_path.append("auto:kv")
            fields.extend(result.fields)
            depth += 1

    if depth < max_depth and not parse_path[1:]:
        delimiter = detect_csv(region, min_columns=4)
        if delimiter:
            result = peel_csv(region, delimiter)
            if not result.error:
                parse_path.append(f"auto:csv({delimiter!r})")
                fields.extend(result.fields)
                depth += 1

    if not parse_path:
        parse_path.append("auto:text")

    return parse_path, fields, region


def _longest_string_field(fields: list[PeeledField]) -> PeeledField | None:
    """The longest JSON string value — the message, in practice."""
    best: PeeledField | None = None
    for candidate in fields:
        if candidate.path == "json.__rest" or not isinstance(candidate.value, str):
            continue
        if best is None or len(candidate.value) > len(str(best.value)):
            best = candidate
    return best


def build(
    decoded: Decoded,
    received_time: str,
    *,
    max_depth: int = 4,
    budget: Budget | None = None,
) -> Tier3Result:
    """Run the cascade, extract tokens, and assemble everything tier 3 contributes.

    ``budget`` short-circuits the expensive halves: if the cascade alone has already used the
    event's time, tokenization is skipped rather than added on top.
    """
    parse_path, fields, region = cascade(decoded, max_depth=max_depth)
    if budget is not None and budget.expired():
        # Out of time after peeling: return what the cascade found, and let the caller downgrade.
        return Tier3Result(
            text=region.text,
            text_span=region.span,
            parse_path=[*parse_path, "budget_exceeded"],
            fields=fields,
            time=parse_time(None, received_time=received_time),
        )
    tokens = extract_tokens(region.text)

    result = Tier3Result(
        text=region.text,
        text_span=region.span,
        parse_path=parse_path,
        fields=fields,
        tokens=tokens,
    )

    # ---- observables, named per A4: ip_1, ip_2, user, host_1, …
    counters: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for token in tokens:
        if token.kind not in INTERESTING:
            continue
        type_id = OBSERVABLE_TYPES.get(token.kind)
        if type_id is None:
            continue
        key = (token.kind, token.value)
        if key in seen:
            continue
        seen.add(key)
        base = {"ip": "ip", "ipv6": "ip", "hostname": "host", "user": "user"}.get(
            token.kind, token.kind
        )
        counters[base] = counters.get(base, 0) + 1
        # A single user is just "user"; several become user_1, user_2 — same for hosts.
        name = base if base == "user" and counters[base] == 1 else f"{base}_{counters[base]}"
        result.observables.append({"name": name, "type_id": type_id, "value": token.value})

        # Every observable is located in the raw bytes, which is what makes tier 3 traceable (P4).
        absolute = (token.start + region.span[0], token.end + region.span[0])
        byte_span = decoded.byte_span(absolute)
        path = f"observables.{name}"
        if byte_span is not None:
            result.field_offsets[path] = byte_span
        else:
            # A lossy decode cannot be trusted for offsets; say derived rather than guess.
            result.derived_fields[path] = "token:no-span"

    # ---- unmapped keeps everything the cascade exposed, plus the tokens worth naming
    for peeled in fields:
        result.unmapped[peeled.path] = peeled.value
    for token in tokens:
        if token.key:
            result.unmapped.setdefault(token.key, token.value)

    # ---- time from the first timestamp token that parses, else arrival
    result.time = _time_from_tokens(tokens, received_time)
    if result.time.source == "event":
        result.derived_fields["time"] = "ts:token"
    else:
        result.derived_fields["time"] = "received"

    # ---- severity from the word vocabulary, and the class hint
    severity = severity_from_words(region.text)
    if severity is not None:
        result.severity_id = severity
        result.derived_fields["severity_id"] = "vocab:severity_words"
    else:
        # Unknown (0) is our default, not something the source said — declare it, so
        # provenance_check has nothing unexplained (P4).
        result.derived_fields["severity_id"] = "default:unknown"

    hint = class_hint(region.text, tokens)
    if hint is not None:
        result.class_hint = hint.as_dict()

    # message is the text field itself, so it is located, not derived.
    message_span = decoded.byte_span(region.span)
    if message_span is not None:
        result.field_offsets["message"] = message_span
    else:
        result.derived_fields["message"] = "text:no-span"

    return result


def _time_from_tokens(tokens: list[Token], received_time: str) -> TimeResult:
    """First timestamp token that parses wins; otherwise the arrival time."""
    for token in tokens:
        if token.kind != "timestamp":
            continue
        parsed = parse_time(token.value, received_time=received_time)
        if parsed.source == "event":
            return parsed
    return parse_time(None, received_time=received_time)
