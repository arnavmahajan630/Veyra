"""Synthetic line generators for the demo engine (B7).

A scenario's ``template: <name>`` names one of these. They build a properly framed line at
the current time, so a generated event is indistinguishable from a real one at the edge —
the ``sshd_failed`` burst in stage 3 is what fires Wazuh rule 100111.

The registry is also what the scenario validator checks a ``template:`` against, so a typo
fails at load instead of sending nothing at run time.
"""

from __future__ import annotations

import datetime
import random
from collections.abc import Callable

# A generator takes the moment to stamp, the scenario's `vary` values for this event, and a
# seeded RNG, and returns one complete line (no trailing newline).
Generator = Callable[[datetime.datetime, dict[str, str], random.Random], str]


def _bsd(now: datetime.datetime) -> str:
    """RFC 3164 timestamp: month, space-padded day, time. No year, by design."""
    return f"{now.strftime('%b')} {now.day:2d} {now.strftime('%H:%M:%S')}"


def sshd_failed(now: datetime.datetime, vary: dict[str, str], rng: random.Random) -> str:
    """A Linux sshd failed-password line, the shape rules 100110-100111 group on."""
    src_ip = vary.get("src_ip", "45.12.3.9")
    user = vary.get("user", "admin")
    port = rng.randint(32768, 60999)
    pid = rng.randint(1000, 9999)
    return (
        f"<86>{_bsd(now)} core-lnx-07 sshd[{pid}]: "
        f"Failed password for invalid user {user} from {src_ip} port {port} ssh2"
    )


def sshd_accepted(now: datetime.datetime, vary: dict[str, str], rng: random.Random) -> str:
    src_ip = vary.get("src_ip", "10.4.1.20")
    user = vary.get("user", "r.patil")
    port = rng.randint(32768, 60999)
    pid = rng.randint(1000, 9999)
    return (
        f"<86>{_bsd(now)} core-lnx-07 sshd[{pid}]: "
        f"Accepted publickey for {user} from {src_ip} port {port} ssh2"
    )


def authsrv_failed(now: datetime.datetime, vary: dict[str, str], rng: random.Random) -> str:
    """The messy Maha Power auth line, including its stack-trace continuation.

    One event over two lines: the edge joins them (IF-FRAMING), and Beat 5 hovers a field
    in exactly this event.
    """
    user = vary.get("user", "a.sharma")
    src_ip = vary.get("src_ip", "103.21.4.77")
    attempts = rng.randint(1, 5)
    return (
        f'<134>{_bsd(now)} fw01 app[233]: {{"evt":"auth","msg":"user={user} FAILED login '
        f'from {src_ip} via 10.2.3.4 attempts:{attempts}"}} | trace=\n'
        f"  at com.x.Auth.login(Auth.java:88)"
    )


GENERATORS: dict[str, Generator] = {
    "sshd_failed": sshd_failed,
    "sshd_accepted": sshd_accepted,
    "authsrv_failed": authsrv_failed,
}


def generate(name: str, now: datetime.datetime, vary: dict[str, str], rng: random.Random) -> str:
    """Build one line from the named generator. An unknown name is a loud error."""
    try:
        generator = GENERATORS[name]
    except KeyError:
        known = ", ".join(sorted(GENERATORS))
        raise KeyError(f"unknown template {name!r}; known templates: {known}") from None
    return generator(now, vary, rng)
