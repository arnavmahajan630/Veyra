"""Corpus handling and traffic senders for the demo engine (B7).

Three things here are load-bearing for the demo and easy to get subtly wrong:

* **An event is not a line.** ``authsrv_t3_failed.log`` carries a stack-trace continuation
  line per event; splitting on newlines would send it as a separate event and Beat 5's
  multi-line event would not exist. A line starting with whitespace continues the one
  before it.
* **Timestamps are rewritten in their own format**, so time parsing and clock skew look
  real. Four formats appear in the corpus: RFC 3164 (``Sep 26 14:05:11``), ISO 8601, CEF's
  ``rt=Sep 26 2026 14:05:00``, and the OT historian's ``26-09-2026 14:05:00``.
* **The syslog host field decides which source an event belongs to** (IF-INVENTORY), so
  one container can simulate every device. CEF lines carry no syslog header at all and get
  one wrapped around them.
"""

from __future__ import annotations

import datetime
import json
import logging
import random
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from veyra_common.settings import Settings

log = logging.getLogger(__name__)

REPO_DIR = Path(__file__).resolve().parents[4]
CORPUS_DIR = REPO_DIR / "demo" / "corpus"

# The host each corpus presents itself as. These must match C1's seed rows, because the
# inventory resolves `syslog_host` to a source id.
CORPUS_HOSTS: dict[str, str] = {
    "acme_ngfw_cef.log": "fw-dmz-01",
    "linux_sshd.log": "core-lnx-07",
    # Deliberately absent from the inventory, so it lands as an unregistered tier 3 source.
    "ot_historian.log": "ot-hist-01",
}

# Corpora that are bare vendor payloads and need an RFC 3164 header wrapped around them,
# which is what a real collector in front of such a device does.
NEEDS_SYSLOG_HEADER = {"acme_ngfw_cef.log"}
# Corpora that carry their own device field instead of a syslog header. Their bytes go on
# the wire exactly as the corpus has them, because the tier-3 goldens and the S0 parity
# vectors are computed from those same bytes; wrapping them would make what the demo shows
# differ from what the engine tests assert.
SELF_IDENTIFYING = {"ot_historian.log"}
DEFAULT_PRI = 134

_BSD_TS = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\b"
)
_ISO_TS = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b")
# CEF's own receipt time, which carries a year: "rt=Sep 26 2026 14:05:00".
_CEF_RT = re.compile(
    r"(?<=rt=)(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2} \d{4} \d{2}:\d{2}:\d{2}"
)
# The OT historian's leading DD-MM-YYYY HH:MM:SS.
_OT_TS = re.compile(r"^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}")


def rewrite_timestamp(text: str, now: datetime.datetime | None = None) -> str:
    """Move every timestamp in one event to ``now``, keeping the format it was written in.

    The rewrite must not change the event's structure: field widths and separators stay as
    they were, because the contracts parse by offset and the console highlights bytes.
    """
    moment = now or datetime.datetime.now(datetime.UTC)
    # Most specific first: the OT prefix and CEF's rt= would both be caught by the looser
    # patterns otherwise.
    if _OT_TS.search(text):
        return _OT_TS.sub(moment.strftime("%d-%m-%Y %H:%M:%S"), text, count=1)
    if _CEF_RT.search(text):
        text = _CEF_RT.sub(
            f"{moment.strftime('%b')} {moment.day} {moment.strftime('%Y %H:%M:%S')}", text, count=1
        )
    if _BSD_TS.search(text):
        bsd = f"{moment.strftime('%b')} {moment.day:2d} {moment.strftime('%H:%M:%S')}"
        return _BSD_TS.sub(bsd, text, count=1)
    if _ISO_TS.search(text):
        return _ISO_TS.sub(moment.isoformat().replace("+00:00", "Z"), text, count=1)
    return text


def apply_vary(text: str, vary: dict[str, str]) -> str:
    """Substitute named values. ``user`` and ``src_ip`` know where they live in a line."""
    result = text
    for key, value in vary.items():
        if key == "user":
            result = re.sub(r"\buser=[^\s,}\"]+", f"user={value}", result)
            result = re.sub(r'"user":\s*"[^"]*"', f'"user":"{value}"', result)
            result = re.sub(r"\b(invalid user|user)\s+\S+", rf"\1 {value}", result)
        elif key == "src_ip":
            # "from <ip>" is how both sshd and the auth server name the source.
            result = re.sub(
                r"\bfrom\s+\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", f"from {value}", result
            )
            result = re.sub(r"\bsrc=\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", f"src={value}", result)
        else:
            # Anything else substitutes a key=value pair of that name, if present.
            result = re.sub(rf"\b{re.escape(key)}=[^\s,;}}\"]+", f"{key}={value}", result)
    return result


def parse_corpus(text: str) -> list[str]:
    """Split a corpus file into *events*, not lines.

    A line that starts with whitespace is a continuation of the event before it (a stack
    trace, a wrapped field). Blank lines separate nothing and are dropped.
    """
    events: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[:1].isspace() and events:
            events[-1] = f"{events[-1]}\n{line.rstrip()}"
        else:
            events.append(line.rstrip())
    return events


def load_corpus(filename: str) -> list[str]:
    """Every event in a corpus file. A missing file is an error, not an empty list."""
    name, _, selector = filename.partition("#")
    path = CORPUS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"no such corpus file: {path}")
    events = parse_corpus(path.read_text(encoding="utf-8"))
    if not selector:
        return events
    # `file#n` picks event n, 1-based, the way the scenario's samples list reads.
    index = int(selector)
    if not (1 <= index <= len(events)):
        raise IndexError(f"{name} has {len(events)} events; #{index} does not exist")
    return [events[index - 1]]


def select_events(
    filename: str,
    *,
    filter_expr: str | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """Load a corpus and apply the scenario's `filter` and `exclude_patterns`."""
    events = load_corpus(filename)
    if filter_expr:
        events = [event for event in events if filter_expr in event]
    for pattern in exclude_patterns or []:
        events = [event for event in events if pattern not in event]
    return events


def wrap_syslog(
    event: str, host: str, pri: int = DEFAULT_PRI, now: datetime.datetime | None = None
) -> str:
    """Put an RFC 3164 header in front of a bare vendor payload."""
    moment = now or datetime.datetime.now(datetime.UTC)
    stamp = f"{moment.strftime('%b')} {moment.day:2d} {moment.strftime('%H:%M:%S')}"
    return f"<{pri}>{stamp} {host} {event}"


def set_syslog_host(event: str, host: str) -> str:
    """Replace the host field of an already-framed RFC 3164 event."""
    return re.sub(
        r"^(<\d{1,3}>(?:\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+)\S+",
        rf"\1{host}",
        event,
        count=1,
    )


@dataclass
class Listener:
    host: str
    port: int
    proto: str


def listeners_for(cfg: Settings) -> dict[str, Listener]:
    """The edge listeners, addressed by the scenario's `listener:` names (IF-PORTS)."""
    return {
        "dmz-udp": Listener(cfg.demo_edge_dmz_host, 5514, "udp"),
        "dmz-tcp": Listener(cfg.demo_edge_dmz_host, 5515, "tcp"),
        "core-udp": Listener(cfg.demo_edge_core_host, 5524, "udp"),
        "core-tcp": Listener(cfg.demo_edge_core_host, 5525, "tcp"),
    }


class Scheduler:
    """Paces a burst of `count` events over `over_s` seconds on the monotonic clock.

    Seeded jitter keeps two runs of the same scenario byte-identical while still looking
    like real traffic. Pacing from a monotonic deadline rather than `sleep(delay)` means a
    slow send does not push the whole burst late.
    """

    def __init__(self, count: int, over_s: float, rng: random.Random) -> None:
        self.count = max(count, 1)
        self.over_s = max(over_s, 0.0)
        self.rng = rng
        self.started = time.monotonic()

    def wait_for(self, index: int) -> None:
        if self.over_s <= 0:
            return
        step = self.over_s / self.count
        # Up to ±25% of one step, so the stream is not metronomic.
        jitter = (self.rng.random() - 0.5) * step * 0.5
        due = self.started + index * step + jitter
        remaining = due - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)


class TrafficSender:
    """Sends events over the real ingestion paths: syslog UDP/TCP and HTTP push (HEC)."""

    def __init__(self, cfg: Settings | None = None, hec_token: str | None = None) -> None:
        self.cfg = cfg or Settings()
        self.listeners = listeners_for(self.cfg)
        self.hec_url = f"{self.cfg.demo_gateway_url.rstrip('/')}/services/collector/event"
        self.hec_token = hec_token
        self._client = httpx.Client(timeout=5.0)

    def close(self) -> None:
        self._client.close()

    # ---------------------------------------------------------------- transports
    def send_udp(self, listener: Listener, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(payload, (listener.host, listener.port))

    def send_tcp_batch(
        self, listener: Listener, payloads: list[bytes], scheduler: Scheduler | None = None
    ) -> int:
        """One connection for the whole burst, the way a real shipper holds one open."""
        sent = 0
        with socket.create_connection((listener.host, listener.port), timeout=5.0) as sock:
            for index, payload in enumerate(payloads):
                if scheduler:
                    scheduler.wait_for(index)
                sock.sendall(payload if payload.endswith(b"\n") else payload + b"\n")
                sent += 1
        return sent

    def send_hec(self, event: str, token: str | None = None) -> None:
        """One JSON object per event, with the multi-line string embedded."""
        tok = token or self.hec_token
        if not tok:
            raise RuntimeError("HEC send needs an API key; stage `key: live` fetches one")
        response = self._client.post(
            self.hec_url,
            headers={"Authorization": f"Splunk {tok}", "Content-Type": "application/json"},
            content=json.dumps({"event": event}),
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"HEC rejected the event: {response.status_code} {response.text[:200]}"
            )

    # ---------------------------------------------------------------- one action
    def build_events(self, cfg: dict[str, Any], rng: random.Random, count: int) -> list[str]:
        """The exact bytes this action will send, in order.

        Separated from sending so a test can assert on them without a socket, and so
        `demo-auto` can prove two seeded runs produce identical traffic.
        """
        from demo_engine.templates import generate_line  # local: keeps demo/ off import time

        corpus = cfg.get("corpus")
        template = cfg.get("template")
        vary_lists: dict[str, list[str]] = cfg.get("vary") or {}
        host_override = cfg.get("host")
        now = datetime.datetime.now(datetime.UTC)

        pool: list[str] = []
        base_name = ""
        if corpus:
            base_name = corpus.partition("#")[0]
            pool = select_events(
                corpus,
                filter_expr=cfg.get("filter") or cfg.get("filter_expr"),
                exclude_patterns=cfg.get("exclude_patterns"),
            )
            if not pool:
                raise ValueError(f"{corpus} has no events left after filtering")

        events: list[str] = []
        for index in range(count):
            vary = {
                key: values[index % len(values)] for key, values in vary_lists.items() if values
            }
            if template:
                event = generate_line(template, now, vary, rng)
            else:
                event = pool[index % len(pool)]
                event = rewrite_timestamp(event, now)
            event = apply_vary(event, vary)

            host = host_override or CORPUS_HOSTS.get(base_name)
            if host and base_name not in SELF_IDENTIFYING:
                event = (
                    wrap_syslog(event, host, now=now)
                    if base_name in NEEDS_SYSLOG_HEADER
                    else set_syslog_host(event, host)
                )
            events.append(event)
        return events

    def dispatch_action(
        self,
        action: dict[str, Any],
        live_key: str | None = None,
        rng: random.Random | None = None,
    ) -> int:
        """Execute one `send:` action. Raises rather than silently sending nothing."""
        cfg = action.get("send", action)
        rng = rng or random.Random(0)
        via = cfg.get("via", "syslog_udp")
        count = int(cfg.get("count", 1))
        over_s = float(cfg.get("over_s", 1.0))
        events = self.build_events(cfg, rng, count)
        scheduler = Scheduler(count, over_s, rng)

        if via == "http_hec_event":
            sent = 0
            for index, event in enumerate(events):
                scheduler.wait_for(index)
                self.send_hec(event, token=live_key)
                sent += 1
            return sent

        name = cfg.get("listener", "core-udp")
        listener = self.listeners.get(name)
        if listener is None:
            raise ValueError(f"unknown listener {name!r}; known: {', '.join(self.listeners)}")

        if via == "syslog_tcp":
            return self.send_tcp_batch(listener, [e.encode() for e in events], scheduler)
        if via == "syslog_udp":
            sent = 0
            for index, event in enumerate(events):
                scheduler.wait_for(index)
                self.send_udp(listener, event.encode())
                sent += 1
            return sent
        raise ValueError(f"unknown transport {via!r}")
