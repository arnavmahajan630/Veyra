"""Establish the event time — without ever reading the clock.

Purity (P3) makes this fiddly and worth its own module: the only "now" the engine may use is
``envelope.received_time``, which arrives as a string. Everything here is a function of the
text plus that reference time, so the same event normalizes identically on any machine at any
time (the determinism test in A3 depends on it).

What IF-ULPF wants recorded: where the time came from (``event`` or ``received``), the assumed
timezone, whether the year was inferred, and the clock skew against arrival.
"""

from __future__ import annotations

from calendar import timegm
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Tried in order when a contract does not name a format. Deliberately short: a contract should
# say what its source emits, and tier 3 falls back to the received time.
AUTO_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d-%m-%Y %H:%M:%S",
    "%b %d %H:%M:%S",  # syslog 3164, no year
    "%b %d %Y %H:%M:%S",  # CEF rt
    "%d/%b/%Y:%H:%M:%S %z",  # apache
)
YEARLESS_FORMATS = frozenset({"%b %d %H:%M:%S"})


@dataclass(slots=True)
class TimeResult:
    """The resolved event time and the provenance IF-ULPF records."""

    epoch_ms: int
    source: str  # "event" | "received"
    tz_assumed: str | None = None
    year_inferred: bool = False
    clock_skew_ms: int | None = None
    format_used: str | None = None


def parse_received(received_time: str) -> datetime:
    """Parse the envelope's RFC3339-with-nanos stamp (always UTC, always present)."""
    head, _, frac = received_time.rstrip("Z").partition(".")
    base = datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    if frac:
        micros = int((frac + "000000")[:6])
        base = base.replace(microsecond=micros)
    return base


def to_epoch_ms(moment: datetime) -> int:
    """UTC epoch milliseconds as an int — never a float (deterministic serialization)."""
    if moment.tzinfo is None:
        return timegm(moment.timetuple()) * 1000 + moment.microsecond // 1000
    return int(moment.timestamp() * 1000)


def _zone(name: str | None) -> tuple[timezone | ZoneInfo, str | None]:
    if not name:
        return UTC, None
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        return UTC, None


def parse_time(
    text: str | None,
    *,
    received_time: str,
    formats: list[str] | None = None,
    tz: str | None = None,
    year_policy: str = "infer_from_received",
) -> TimeResult:
    """Resolve the event time from ``text``, falling back to the arrival time.

    ``year_policy`` is ``infer_from_received`` (syslog 3164 has no year: pick the year that
    puts the event at or before arrival, allowing a day of clock skew) or ``present``.
    """
    received = parse_received(received_time)
    received_ms = to_epoch_ms(received)

    if not text or not str(text).strip():
        return TimeResult(epoch_ms=received_ms, source="received")

    candidate = str(text).strip()
    tzinfo, tz_name = _zone(tz)

    for fmt in list(formats or ()) + list(AUTO_FORMATS):
        try:
            parsed = datetime.strptime(candidate, fmt)
        except (ValueError, TypeError):
            continue

        year_inferred = False
        if fmt in YEARLESS_FORMATS or parsed.year == 1900:
            if year_policy == "infer_from_received":
                parsed = parsed.replace(year=received.year)
                localized = parsed.replace(tzinfo=tzinfo) if parsed.tzinfo is None else parsed
                # An event stamped after arrival (plus a day of tolerance) must be from the
                # previous year — December logs read in January.
                if to_epoch_ms(localized) > received_ms + 86_400_000:
                    parsed = parsed.replace(year=received.year - 1)
                year_inferred = True
            else:
                parsed = parsed.replace(year=received.year)
                year_inferred = True

        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tzinfo)
            assumed = tz_name or "UTC"
        else:
            assumed = None

        epoch_ms = to_epoch_ms(parsed)
        return TimeResult(
            epoch_ms=epoch_ms,
            source="event",
            tz_assumed=assumed,
            year_inferred=year_inferred,
            clock_skew_ms=received_ms - epoch_ms,
            format_used=fmt,
        )

    # Nothing parsed: use arrival, and say so rather than guessing.
    return TimeResult(epoch_ms=received_ms, source="received")
