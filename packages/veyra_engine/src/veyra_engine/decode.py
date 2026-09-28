"""Decode raw bytes to text, and keep the map back to byte offsets.

Everything downstream works on **characters** (regexes, JSON scanning, templates), but
``ulpf.field_offsets`` must be **byte** offsets into the raw event, because that is what a
verifier slices and what the console highlights (P4, IF-ULPF). A Devanagari line is 42
characters and 50 bytes, so the two are not interchangeable — this module is the bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from charset_normalizer import from_bytes


@dataclass(slots=True)
class Decoded:
    """Decoded text plus everything needed to translate spans back to bytes."""

    text: str
    encoding: str
    confidence: float
    invalid_bytes: int
    raw: bytes
    # char index -> byte offset, with one extra entry so the end of the text maps too.
    char_to_byte: list[int] = field(default_factory=list)
    lossy: bool = False

    def byte_span(self, char_span: tuple[int, int] | None) -> tuple[int, int] | None:
        """Translate a ``[start, end)`` character span into a byte span.

        Returns ``None`` when the span cannot be trusted — out of range, or the decode was
        lossy so offsets no longer line up with the original bytes. A missing offset is
        honest; a wrong one would break provenance.
        """
        if char_span is None or self.lossy:
            return None
        start, end = char_span
        if start < 0 or end < start or end >= len(self.char_to_byte):
            return None
        return self.char_to_byte[start], self.char_to_byte[end]

    def slice_bytes(self, char_span: tuple[int, int]) -> bytes:
        """The raw bytes a character span came from (used by tests and verification)."""
        span = self.byte_span(char_span)
        if span is None:
            return b""
        return self.raw[span[0] : span[1]]


def _build_offsets(text: str, encoding: str) -> tuple[list[int], bool]:
    """Cumulative byte offset of every character; ``(offsets, exact)``."""
    offsets = [0] * (len(text) + 1)
    total = 0
    try:
        for i, char in enumerate(text):
            offsets[i] = total
            total += len(char.encode(encoding))
    except (UnicodeEncodeError, LookupError):
        # The text cannot be re-encoded in the detected codec, so character spans cannot be
        # mapped to byte offsets truthfully. Say so instead of guessing.
        return offsets, False
    offsets[len(text)] = total
    return offsets, True


def decode_text_only(raw: bytes) -> str:
    """Just the text, with no offset map.

    Building ``char_to_byte`` costs one Python int per character, which on a 200 KB event is most of
    the per-event budget. Callers that only need the text for ``raw_data`` — where no span is ever
    reported — use this instead.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def decode(raw: bytes) -> Decoded:
    """UTF-8 strict, then a best-guess codec, then lossy replacement.

    ``invalid_bytes`` counts replacement characters, which is what IF-ULPF's
    ``encoding.invalid_bytes`` reports.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        offsets, exact = _build_offsets(text, "utf-8")
        return Decoded(
            text=text,
            encoding="utf-8",
            confidence=1.0,
            invalid_bytes=0,
            raw=raw,
            char_to_byte=offsets,
            lossy=not exact,
        )

    best = from_bytes(raw).best()
    if best is not None and best.encoding:
        text = str(best)
        encoding = best.encoding
        offsets, exact = _build_offsets(text, encoding)
        if exact and len(raw) == offsets[-1]:
            return Decoded(
                text=text,
                encoding=encoding,
                confidence=max(0.0, 1.0 - float(best.chaos)),
                invalid_bytes=0,
                raw=raw,
                char_to_byte=offsets,
                lossy=False,
            )

    # Last resort: never fail on bad bytes (P2). Offsets are marked untrustworthy, so the
    # engine will report the fields as derived rather than claim a byte range.
    text = raw.decode("utf-8", errors="replace")
    offsets, _ = _build_offsets(text, "utf-8")
    return Decoded(
        text=text,
        encoding="utf-8",
        confidence=0.0,
        invalid_bytes=text.count("�"),
        raw=raw,
        char_to_byte=offsets,
        lossy=True,
    )
