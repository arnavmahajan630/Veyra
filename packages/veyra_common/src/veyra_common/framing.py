"""Event framing shared by the edge (A1) and the HTTP gateway (A2).

One rule, one implementation: a line that begins with whitespace, or with ``at ``
(JVM stack frames), continues the previous line. Joined events keep the original
bytes, joined with a single ``\\n``, so ``raw_sha256`` stays meaningful and byte
offsets in ``ulpf.field_offsets`` still point at real bytes (P1, P4).

Everything here works on **bytes**. Decoding happens later, in the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

CONTINUATION_PREFIXES: Final[tuple[bytes, ...]] = (b"at ",)
_WHITESPACE: Final[bytes] = b" \t"


@dataclass(slots=True)
class FramedEvent:
    """One logical event carved out of a byte stream."""

    raw: bytes
    parts: int = 1
    truncated: bool = False
    method: str = "newline"
    lines: list[bytes] = field(default_factory=list)


def is_continuation(line: bytes) -> bool:
    """True if ``line`` continues the previous logical event."""
    if not line:
        return False
    if line[0:1] in (b" ", b"\t"):
        return True
    stripped = line.lstrip(_WHITESPACE)
    return stripped.startswith(CONTINUATION_PREFIXES)


def split_lines(data: bytes, *, max_event_bytes: int | None = None) -> list[FramedEvent]:
    """Split a byte blob into logical events.

    Newline-delimited, with the continuation rule applied. Empty lines are dropped
    unless they are inside a continuation run. When ``max_event_bytes`` is given,
    an over-long event is cut and flagged ``truncated``; the caller keeps the full
    bytes for the archive when it has them (IF-ENVELOPE ``framing.truncated``).
    """
    events: list[FramedEvent] = []
    for line in data.split(b"\n"):
        line = line.removesuffix(b"\r")
        if is_continuation(line) and events:
            cur = events[-1]
            cur.lines.append(line)
            cur.raw = b"\n".join(cur.lines)
            cur.parts += 1
            cur.method = "multiline_join"
            continue
        if not line.strip():
            continue
        events.append(FramedEvent(raw=line, parts=1, method="newline", lines=[line]))

    if max_event_bytes is not None:
        for ev in events:
            if len(ev.raw) > max_event_bytes:
                ev.raw = ev.raw[:max_event_bytes]
                ev.truncated = True
    return events


def frame_datagram(data: bytes, *, max_event_bytes: int | None = None) -> FramedEvent:
    """A UDP datagram is exactly one event (``framing.method="datagram"``)."""
    truncated = False
    if max_event_bytes is not None and len(data) > max_event_bytes:
        data = data[:max_event_bytes]
        truncated = True
    return FramedEvent(raw=data, parts=1, truncated=truncated, method="datagram", lines=[data])
