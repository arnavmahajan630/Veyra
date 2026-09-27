"""A JSON scanner that records where every scalar came from.

``json.loads`` throws the positions away, and positions are the whole point: a mapped value
like ``user.name`` has to point at the exact bytes inside the raw event (P4). So this is a
small recursive-descent parser that returns the value *and* a ``path -> char span`` map.

Two deliberate details, both from IF-CONTRACT-YAML / IF-ULPF:

* string values are **unescaped** in the returned value, but their span covers the **raw**
  (still escaped) text between the quotes — so the span slices to what a human sees in the
  raw event, byte for byte;
* text after the first complete value is not an error. It is returned as ``rest`` with its
  own span, because real logs append things like ``| trace=`` after a JSON body, and A4
  kv-parses that remainder.

Paths look like ``msg``, ``a.b``, ``items[0].id`` — the same shape contracts use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WHITESPACE = " \t\n\r"


class SpanJsonError(ValueError):
    """The text is not JSON at the position we were told to start from."""


@dataclass(slots=True)
class JsonScan:
    """Result of scanning one JSON value out of a larger string."""

    value: Any
    spans: dict[str, tuple[int, int]] = field(default_factory=dict)
    end: int = 0
    rest: str | None = None
    rest_span: tuple[int, int] | None = None


class _Scanner:
    """One pass over the text. Not reused; construct per scan."""

    __slots__ = ("offset", "pos", "spans", "text")

    def __init__(self, text: str, offset: int = 0) -> None:
        self.text = text
        self.pos = 0
        self.spans: dict[str, tuple[int, int]] = {}
        self.offset = offset  # added to every recorded span, for nested/offset scans

    # ---------------------------------------------------------------- helpers
    def _skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in WHITESPACE:
            self.pos += 1

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _expect(self, char: str) -> None:
        if self._peek() != char:
            raise SpanJsonError(f"expected {char!r} at {self.pos}, found {self._peek()!r}")
        self.pos += 1

    def _record(self, path: str, start: int, end: int) -> None:
        if path:
            self.spans[path] = (start + self.offset, end + self.offset)

    # ---------------------------------------------------------------- values
    def parse_value(self, path: str) -> Any:
        self._skip_ws()
        char = self._peek()
        if char == "{":
            return self._parse_object(path)
        if char == "[":
            return self._parse_array(path)
        if char == '"':
            return self._parse_string(path)
        if char == "":
            raise SpanJsonError("unexpected end of input")
        return self._parse_literal(path)

    def _parse_object(self, path: str) -> dict[str, Any]:
        self._expect("{")
        out: dict[str, Any] = {}
        self._skip_ws()
        if self._peek() == "}":
            self.pos += 1
            return out
        while True:
            self._skip_ws()
            if self._peek() != '"':
                raise SpanJsonError(f"expected a key at {self.pos}")
            key = self._parse_string("")  # keys are not values; no span recorded
            self._skip_ws()
            self._expect(":")
            child = f"{path}.{key}" if path else key
            out[key] = self.parse_value(child)
            self._skip_ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            self._expect("}")
            return out

    def _parse_array(self, path: str) -> list[Any]:
        self._expect("[")
        out: list[Any] = []
        self._skip_ws()
        if self._peek() == "]":
            self.pos += 1
            return out
        index = 0
        while True:
            out.append(self.parse_value(f"{path}[{index}]"))
            index += 1
            self._skip_ws()
            if self._peek() == ",":
                self.pos += 1
                continue
            self._expect("]")
            return out

    def _parse_string(self, path: str) -> str:
        self._expect('"')
        start = self.pos  # first char inside the quotes: the raw, escaped content
        chunks: list[str] = []
        while True:
            char = self._peek()
            if char == "":
                raise SpanJsonError("unterminated string")
            if char == '"':
                end = self.pos
                self.pos += 1
                self._record(path, start, end)
                return "".join(chunks)
            if char == "\\":
                self.pos += 1
                esc = self._peek()
                if esc == "":
                    raise SpanJsonError("dangling escape")
                self.pos += 1
                if esc == "u":
                    hexits = self.text[self.pos : self.pos + 4]
                    if len(hexits) < 4:
                        raise SpanJsonError("short \\u escape")
                    self.pos += 4
                    code = int(hexits, 16)
                    # Surrogate pair: JSON encodes astral characters as two \u escapes.
                    if 0xD800 <= code <= 0xDBFF and self.text[self.pos : self.pos + 2] == "\\u":
                        low_hexits = self.text[self.pos + 2 : self.pos + 6]
                        if len(low_hexits) == 4:
                            low = int(low_hexits, 16)
                            if 0xDC00 <= low <= 0xDFFF:
                                self.pos += 6
                                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    chunks.append(chr(code))
                else:
                    chunks.append(
                        {
                            '"': '"',
                            "\\": "\\",
                            "/": "/",
                            "b": "\b",
                            "f": "\f",
                            "n": "\n",
                            "r": "\r",
                            "t": "\t",
                        }.get(esc, esc)
                    )
                continue
            chunks.append(char)
            self.pos += 1

    def _parse_literal(self, path: str) -> Any:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] not in ",}] \t\n\r":
            self.pos += 1
        token = self.text[start : self.pos]
        if not token:
            raise SpanJsonError(f"empty literal at {start}")
        self._record(path, start, self.pos)
        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        try:
            if any(c in token for c in ".eE") and token not in {"-", "+"}:
                return float(token)
            return int(token)
        except ValueError as exc:
            raise SpanJsonError(f"invalid literal {token!r} at {start}") from exc


def scan(text: str, start: int = 0, *, path_prefix: str = "") -> JsonScan:
    """Scan the first complete JSON value at or after ``start``.

    ``path_prefix`` prefixes every recorded path (the peel layers use ``"json"``), and spans
    are reported in the coordinates of ``text`` — so a caller that sliced a body out of a
    bigger string passes the body's own offset in ``start``.
    """
    scanner = _Scanner(text[start:], offset=start)
    value = scanner.parse_value(path_prefix)
    end = start + scanner.pos

    rest = text[end:].strip()
    rest_span: tuple[int, int] | None = None
    if rest:
        lead = len(text[end:]) - len(text[end:].lstrip())
        rest_span = (end + lead, end + lead + len(rest))

    return JsonScan(
        value=value,
        spans=scanner.spans,
        end=end,
        rest=rest or None,
        rest_span=rest_span,
    )


def looks_like_json(text: str) -> bool:
    """Cheap check used by the classifier cascade: does the body start a JSON value?"""
    stripped = text.lstrip(WHITESPACE)
    return stripped[:1] in ("{", "[")
