"""Envelope peeling: strip transport wrappers, exposing fields with spans as we go.

A real log line is layered — syslog around JSON around a message that itself contains
``key=value`` pairs. Contracts declare that order (IF-CONTRACT-YAML ``envelope:``) and this
module applies it, recording for every exposed value the character span it came from, so the
mapper can turn it into a byte offset (P4).

Each layer answers two questions: *does this text look like me* (``detect``) and *what fields
and body does it expose* (``parse``). The cascade in A4 uses ``detect``; a contract-driven
run just calls the layers in the declared order.

RE2 only (no ``re``), per A4's robustness rule: patterns are linear-time and cannot blow up
on a hostile line.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

import re2

from veyra_engine.spanjson import SpanJsonError, looks_like_json, scan

# ---------------------------------------------------------------- re2 helpers
# google-re2 rejects match.span("name"), so spans go through the group index.


def named_span(match: Any, pattern: Any, name: str) -> tuple[int, int] | None:
    index = pattern.groupindex.get(name)
    if index is None:
        return None
    start, end = match.span(index)
    return None if start < 0 else (start, end)


# One layer may expose at most this many fields. A line with a thousand key=value pairs is noise
# past the first couple of hundred, and exposing them all cost ~17 ms — over three times the
# per-event budget (A4 AC4). Fields are taken in document order, and `raw_data` keeps everything.
MAX_FIELDS_PER_LAYER = 256


@dataclass(slots=True)
class PeeledField:
    """One value a layer exposed, with where it came from."""

    path: str
    value: Any
    char_span: tuple[int, int] | None
    layer: str


@dataclass(slots=True)
class Region:
    """The part of the text the next layer should look at."""

    text: str
    span: tuple[int, int]

    @property
    def start(self) -> int:
        return self.span[0]


@dataclass(slots=True)
class LayerResult:
    """What a layer produced."""

    fields: list[PeeledField] = dataclass_field(default_factory=list)
    body: Region | None = None
    # A layer may nominate the text that templates should match against (json text_field).
    text_field: Region | None = None
    error: str | None = None


# ---------------------------------------------------------------- syslog
_RE_5424 = re2.compile(
    r"(?s)^<(?P<pri>\d{1,3})>(?P<version>\d)\s+(?P<timestamp>\S+)\s+(?P<host>\S+)\s+"
    r"(?P<app>\S+)\s+(?P<procid>\S+)\s+(?P<msgid>\S+)\s+(?P<sd>(?:\[[^\]]*\])+|-)\s?(?P<body>.*)$"
)
_RE_3164 = re2.compile(
    r"(?s)^<(?P<pri>\d{1,3})>(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?:(?P<app>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s*)?(?P<body>.*)$"
)
# Some senders omit the priority entirely.
_RE_3164_NOPRI = re2.compile(
    r"(?s)^(?P<timestamp>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\s+"
    r"(?P<host>\S+)\s+(?:(?P<app>[^\s\[:]+)(?:\[(?P<pid>\d+)\])?:\s*)?(?P<body>.*)$"
)


def detect_syslog(region: Region) -> str | None:
    """``"rfc5424"``, ``"rfc3164"`` or ``None``."""
    text = region.text
    if _RE_5424.match(text):
        return "rfc5424"
    if _RE_3164.match(text) or _RE_3164_NOPRI.match(text):
        return "rfc3164"
    return None


# A structured payload whose name happens to look like an RFC3164 tag: `CEF:0|...` and
# `LEEF:1.0|...`. Without this, `CEF` is read as the program name and `0|Acme|NGFW|...` as the
# message, so the next layer is handed a body with its own header already eaten — which is why
# syslog-framed CEF could not be parsed by a CEF contract at all.
_RE_PAYLOAD_TAG = re2.compile(r"^(?:CEF|LEEF):\d")


def peel_syslog(region: Region, variant: str = "auto") -> LayerResult:
    """Expose ``syslog.*`` fields and hand the message body to the next layer."""
    candidates: list[tuple[str, Any]] = []
    if variant in ("auto", "rfc5424"):
        candidates.append(("rfc5424", _RE_5424))
    if variant in ("auto", "rfc3164"):
        candidates.append(("rfc3164", _RE_3164))
        candidates.append(("rfc3164", _RE_3164_NOPRI))

    for name, pattern in candidates:
        match = pattern.match(region.text)
        if not match:
            continue
        fields: list[PeeledField] = []
        for group in ("pri", "version", "timestamp", "host", "app", "pid", "procid", "msgid", "sd"):
            if group not in pattern.groupindex:
                continue
            value = match.group(pattern.groupindex[group])
            if value is None:
                continue
            span = named_span(match, pattern, group)
            absolute = None if span is None else (span[0] + region.start, span[1] + region.start)
            fields.append(
                PeeledField(path=f"syslog.{group}", value=value, char_span=absolute, layer=name)
            )

        # facility and severity are arithmetic on pri, so they are derived, not located.
        if "pri" in pattern.groupindex:
            pri_text = match.group(pattern.groupindex["pri"])
            if pri_text is not None and pri_text.isdigit():
                pri = int(pri_text)
                fields.append(PeeledField("syslog.facility", pri // 8, None, f"{name}:derived"))
                fields.append(PeeledField("syslog.severity", pri % 8, None, f"{name}:derived"))

        body_span = named_span(match, pattern, "body")
        app_span = named_span(match, pattern, "app") if "app" in pattern.groupindex else None
        # `CEF:0|...` is a payload, not a tag plus a message. Give the whole thing back as the
        # body and drop the tag field, so the CEF layer sees the header it needs.
        if app_span is not None and body_span is not None:
            app_value = match.group(pattern.groupindex["app"]) or ""
            if _RE_PAYLOAD_TAG.match(f"{app_value}:{match.group(pattern.groupindex['body'])}"):
                body_span = (app_span[0], body_span[1])
                fields = [f for f in fields if f.path not in ("syslog.app", "syslog.pid")]

        body = None
        if body_span is not None:
            body = Region(
                text=region.text[body_span[0] : body_span[1]],
                span=(body_span[0] + region.start, body_span[1] + region.start),
            )
        return LayerResult(fields=fields, body=body, text_field=body)

    return LayerResult(error="not syslog")


# ---------------------------------------------------------------- json
def peel_json(region: Region, text_field: str | None = None) -> LayerResult:
    """Parse a JSON body, exposing every scalar with its span.

    ``text_field`` names the field whose value templates should match against — that is how
    the demo's ``authsrv`` contract says "the real message is inside ``.msg``".
    """
    if not looks_like_json(region.text):
        return LayerResult(error="body does not start a JSON value")
    lead = len(region.text) - len(region.text.lstrip(" \t\n\r"))
    try:
        result = scan(region.text, lead, path_prefix="json")
    except SpanJsonError as exc:
        return LayerResult(error=f"invalid json: {exc}")

    fields: list[PeeledField] = []
    for path, span in result.spans.items():
        value = _dig(result.value, path.removeprefix("json."))
        fields.append(
            PeeledField(
                path=path,
                value=value,
                char_span=(span[0] + region.start, span[1] + region.start),
                layer="json",
            )
        )

    # Trailing text is data, not an error: `... } | trace=` is exactly the demo's T3 shape.
    if result.rest and result.rest_span:
        fields.append(
            PeeledField(
                path="json.__rest",
                value=result.rest,
                char_span=(
                    result.rest_span[0] + region.start,
                    result.rest_span[1] + region.start,
                ),
                layer="json",
            )
        )

    chosen: Region | None = None
    if text_field:
        wanted = f"json.{text_field}"
        for candidate in fields:
            if candidate.path == wanted and isinstance(candidate.value, str):
                if candidate.char_span is not None:
                    chosen = Region(text=candidate.value, span=candidate.char_span)
                break
    return LayerResult(fields=fields, body=chosen, text_field=chosen)


def _dig(value: Any, path: str) -> Any:
    """Follow a ``a.b[0].c`` path into a parsed JSON value."""
    if not path:
        return value
    current = value
    token = ""
    index_digits = ""
    reading_index = False
    for char in path + ".":
        if reading_index:
            if char == "]":
                current = current[int(index_digits)] if isinstance(current, list) else None
                index_digits = ""
                reading_index = False
            else:
                index_digits += char
            continue
        if char == "[":
            if token:
                current = current.get(token) if isinstance(current, dict) else None
                token = ""
            reading_index = True
            continue
        if char == ".":
            if token:
                current = current.get(token) if isinstance(current, dict) else None
                token = ""
            continue
        token += char
    return current


# ---------------------------------------------------------------- kv
_RE_KV_PAIR = re2.compile(r"([A-Za-z_][\w.\-]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s]+)")
# CEF and LEEF extensions allow spaces inside values: a value ends where the next `key=`
# begins (CEF spec). `rt=Sep 26 2026 14:05:00 src=1.2.3.4` is two pairs, not six. RE2 has no
# lookahead (by design — that is what keeps it linear), so the boundaries are found by scanning
# for the keys and slicing between them.
_RE_KV_KEY = re2.compile(r"(?:^|\s)([A-Za-z_][\w.\-]*)\s*=\s*")


def _spaced_pairs(text: str) -> list[tuple[str, int, int]]:
    """``(key, value_start, value_end)`` for a CEF/LEEF-style extension.

    One pass: each key's value runs from just after its ``=`` to the start of the next key.
    """
    found = [(m.group(1), m.start(1), m.end()) for m in _RE_KV_KEY.finditer(text)]
    return [
        (key, value_start, found[index + 1][1] if index + 1 < len(found) else len(text))
        for index, (key, _key_start, value_start) in enumerate(found)
    ]


def detect_kv(region: Region, *, min_pairs: int = 2, density: float = 0.3) -> bool:
    """True when enough of the line is ``key=value`` to treat it as kv."""
    pairs = list(_RE_KV_PAIR.finditer(region.text))
    if len(pairs) < min_pairs:
        return False
    tokens = max(1, len(region.text.split()))
    return len(pairs) / tokens >= density


def peel_kv(region: Region, prefix: str = "kv", *, spaced_values: bool = False) -> LayerResult:
    """Expose every ``key=value`` pair, unquoting values but spanning the raw text.

    ``spaced_values`` switches to the CEF/LEEF rule where a value continues until the next key.
    """
    fields: list[PeeledField] = []
    if spaced_values:
        pairs = _spaced_pairs(region.text)
    else:
        pairs = [
            (m.group(1), m.span(2)[0], m.span(2)[1]) for m in _RE_KV_PAIR.finditer(region.text)
        ]
    for key, start, end in pairs[:MAX_FIELDS_PER_LAYER]:
        raw_value = region.text[start:end].rstrip()
        end = start + len(raw_value)
        value = raw_value
        if len(raw_value) >= 2 and raw_value[0] in "\"'" and raw_value[-1] == raw_value[0]:
            value = raw_value[1:-1]
            start, end = start + 1, end - 1
        fields.append(
            PeeledField(
                path=f"{prefix}.{key}",
                value=value,
                char_span=(start + region.start, end + region.start),
                layer="kv",
            )
        )
    if not fields:
        return LayerResult(error="no key=value pairs")
    return LayerResult(fields=fields, body=region, text_field=region)


# ---------------------------------------------------------------- cef / leef
_RE_CEF_HEADER = re2.compile(
    r"^CEF:(?P<cef_version>\d+)\|(?P<device_vendor>(?:[^|\\]|\\.)*)\|"
    r"(?P<device_product>(?:[^|\\]|\\.)*)\|(?P<device_version>(?:[^|\\]|\\.)*)\|"
    r"(?P<signature_id>(?:[^|\\]|\\.)*)\|(?P<name>(?:[^|\\]|\\.)*)\|(?P<severity>(?:[^|\\]|\\.)*)\|"
)


def detect_cef(region: Region) -> bool:
    return region.text.lstrip().startswith("CEF:")


def peel_cef(region: Region) -> LayerResult:
    """CEF header fields plus the extension's ``key=value`` pairs."""
    text = region.text
    lead = len(text) - len(text.lstrip())
    match = _RE_CEF_HEADER.match(text[lead:])
    if not match:
        return LayerResult(error="not a CEF header")

    fields: list[PeeledField] = []
    for group in (
        "cef_version",
        "device_vendor",
        "device_product",
        "device_version",
        "signature_id",
        "name",
        "severity",
    ):
        span = named_span(match, _RE_CEF_HEADER, group)
        value = match.group(_RE_CEF_HEADER.groupindex[group])
        shift = lead + region.start
        absolute = None if span is None else (span[0] + shift, span[1] + shift)
        fields.append(PeeledField(f"cef.{group}", value, absolute, "cef"))

    extension_start = lead + match.end()
    extension = Region(
        text=text[extension_start:],
        span=(extension_start + region.start, region.span[1]),
    )
    ext = peel_kv(extension, prefix="cef", spaced_values=True)
    fields.extend(ext.fields)
    # The name field is the human-readable event, and what templates match on.
    name_field = next((f for f in fields if f.path == "cef.name"), None)
    chosen = (
        Region(text=str(name_field.value), span=name_field.char_span)
        if name_field and name_field.char_span
        else extension
    )
    return LayerResult(fields=fields, body=extension, text_field=chosen)


_RE_LEEF_HEADER = re2.compile(
    r"^LEEF:(?P<leef_version>\d+\.\d+)\|(?P<vendor>[^|]*)\|(?P<product>[^|]*)\|"
    r"(?P<version>[^|]*)\|(?P<event_id>[^|]*)\|"
)


def detect_leef(region: Region) -> bool:
    return region.text.lstrip().startswith("LEEF:")


def peel_leef(region: Region) -> LayerResult:
    """LEEF header plus tab-separated ``key=value`` attributes."""
    text = region.text
    lead = len(text) - len(text.lstrip())
    match = _RE_LEEF_HEADER.match(text[lead:])
    if not match:
        return LayerResult(error="not a LEEF header")

    fields: list[PeeledField] = []
    for group in ("leef_version", "vendor", "product", "version", "event_id"):
        span = named_span(match, _RE_LEEF_HEADER, group)
        value = match.group(_RE_LEEF_HEADER.groupindex[group])
        shift = lead + region.start
        absolute = None if span is None else (span[0] + shift, span[1] + shift)
        fields.append(PeeledField(f"leef.{group}", value, absolute, "leef"))

    attrs_start = lead + match.end()
    attrs = Region(text=text[attrs_start:], span=(attrs_start + region.start, region.span[1]))
    fields.extend(peel_kv(attrs, prefix="leef", spaced_values=True).fields)
    return LayerResult(fields=fields, body=attrs, text_field=attrs)


# ---------------------------------------------------------------- csv
def peel_csv(region: Region, delimiter: str = ",", header: list[str] | None = None) -> LayerResult:
    """Split a delimited line, naming columns from the contract's header when given.

    Hand-written rather than the ``csv`` module because we need the span of every column, and
    these lines are single-record (a log line), not a file.
    """
    fields: list[PeeledField] = []
    position = 0
    index = 0
    text = region.text
    while position <= len(text) and index < MAX_FIELDS_PER_LAYER:
        end = text.find(delimiter, position)
        if end == -1:
            end = len(text)
        raw_value = text[position:end]
        value = raw_value.strip()
        offset = len(raw_value) - len(raw_value.lstrip())
        name = header[index] if header and index < len(header) else f"col{index}"
        span = (position + offset + region.start, position + offset + len(value) + region.start)
        fields.append(PeeledField(f"csv.{name}", value, span, "csv"))
        index += 1
        if end == len(text):
            break
        position = end + len(delimiter)
    if len(fields) <= 1:
        return LayerResult(error="no delimiter found")
    return LayerResult(fields=fields, body=region, text_field=region)


def detect_csv(region: Region, *, min_columns: int = 4) -> str | None:
    """Return the delimiter that splits this line into enough columns, if any."""
    best: tuple[int, str] | None = None
    for delimiter in (";", "|", "\t", ","):
        count = region.text.count(delimiter)
        if count >= min_columns - 1 and (best is None or count > best[0]):
            best = (count, delimiter)
    return best[1] if best else None


# ---------------------------------------------------------------- regex / base64
def peel_regex(region: Region, pattern: str) -> LayerResult:
    """A contract-supplied RE2 with named groups."""
    try:
        compiled = re2.compile(pattern)
    except Exception as exc:  # a bad contract pattern must not crash the engine
        return LayerResult(error=f"invalid regex: {exc}")
    match = compiled.search(region.text)
    if not match:
        return LayerResult(error="regex did not match")
    fields: list[PeeledField] = []
    for name in compiled.groupindex:
        span = named_span(match, compiled, name)
        value = match.group(compiled.groupindex[name])
        absolute = None if span is None else (span[0] + region.start, span[1] + region.start)
        fields.append(PeeledField(f"regex.{name}", value, absolute, "regex"))
    return LayerResult(fields=fields, body=region, text_field=region)


def peel_base64(region: Region, source_field: str, fields_so_far: list[PeeledField]) -> LayerResult:
    """Decode a base64 field. Inner values get no span — they are not in the raw bytes.

    IF-ULPF is explicit about this: anything that is not literally present in the raw event is
    ``derived``, never given a byte range.
    """
    import base64 as b64

    source = next((f for f in fields_so_far if f.path == source_field), None)
    if source is None or not isinstance(source.value, str):
        return LayerResult(error=f"no such field to decode: {source_field}")
    try:
        decoded = b64.b64decode(source.value, validate=True).decode("utf-8", errors="replace")
    except Exception as exc:  # malformed base64 is data, not a crash
        return LayerResult(error=f"invalid base64: {exc}")
    inner = Region(text=decoded, span=(0, len(decoded)))
    return LayerResult(
        fields=[PeeledField("base64.decoded", decoded, None, "base64")],
        body=inner,
        text_field=inner,
    )


# ---------------------------------------------------------------- orchestration
@dataclass(slots=True)
class PeelOutcome:
    """Everything one peel run produced."""

    layers: list[str] = dataclass_field(default_factory=list)
    fields: list[PeeledField] = dataclass_field(default_factory=list)
    text_field: Region | None = None
    depth: int = 0
    error: str | None = None

    def field_map(self) -> dict[str, PeeledField]:
        """Exposed fields by path; later layers win on a name clash."""
        return {f.path: f for f in self.fields}


def run_layers(
    text: str,
    specs: list[dict[str, Any]],
    *,
    max_depth: int = 4,
) -> PeelOutcome:
    """Apply ``envelope:`` layers in order (IF-CONTRACT-YAML).

    Each spec is a single-key dict, e.g. ``{"syslog": {"variant": "auto"}}``. A layer that
    fails to apply stops the cascade and its reason is reported — the caller decides the tier
    (a failed peel with a contract present is tier 2, not a crash).

    A layer may declare ``optional: true``, which means "peel this if it is there". The
    cascade then continues on the same region instead of stopping, and the layer is left out
    of ``layers`` so the parse path still says what was actually peeled. One source really
    does arrive both ways: a firewall's CEF carries a syslog header over the syslog listener
    and none when the vendor pushes it, and both are the same format with the same contract.
    """
    outcome = PeelOutcome()
    region = Region(text=text, span=(0, len(text)))
    outcome.text_field = region

    for spec in specs[:max_depth]:
        if not spec:
            continue
        name = next(iter(spec))
        options = spec[name] or {}
        result: LayerResult

        if name == "syslog":
            result = peel_syslog(region, options.get("variant", "auto"))
        elif name == "json":
            result = peel_json(region, options.get("text_field"))
        elif name == "kv":
            result = peel_kv(region, options.get("prefix", "kv"))
        elif name == "cef":
            result = peel_cef(region)
        elif name == "leef":
            result = peel_leef(region)
        elif name == "csv":
            result = peel_csv(region, options.get("delimiter", ","), options.get("header"))
        elif name == "regex":
            result = peel_regex(region, options.get("pattern", ""))
        elif name == "base64":
            result = peel_base64(region, options.get("field", ""), outcome.fields)
        else:
            outcome.error = f"unknown envelope layer: {name}"
            break

        if result.error:
            if options.get("optional"):
                # Not present on this line. Leave the region alone and try the next layer.
                continue
            outcome.error = f"{name}: {result.error}"
            break

        outcome.layers.append(name)
        outcome.fields.extend(result.fields)
        outcome.depth += 1
        if result.text_field is not None:
            outcome.text_field = result.text_field
        if result.body is not None:
            region = result.body

    if len(specs) > max_depth:
        outcome.error = f"peel depth capped at {max_depth} ({len(specs)} layers declared)"
    return outcome
