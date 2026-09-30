"""Send demo corpus lines to the Vector edges over real syslog sockets.

This is the path a device takes (A1), unlike fake_raw.py which writes envelopes straight to
Kafka. Each corpus file is sent the way its source would send it, so the edge's inventory
(edge/vector/inventory/sources.csv) resolves it:

    linux_sshd.log     UDP  -> edge-core :5524  (syslog host core-lnx-07 -> src_lnx_core_07)
    acme_ngfw_cef.log  TCP  -> edge-dmz  :5515  (wrapped in a syslog header, host fw-dmz-01)
    ot_historian.log   UDP  -> edge-dmz  :5514  (no header: an unregistered sender)
    garbage.bin        UDP  -> edge-dmz  :5514  (binary chunks: tier 4)

    python demo/tools/send_syslog.py                      # every file once
    python demo/tools/send_syslog.py --file linux_sshd.log --repeat 3
    python demo/tools/send_syslog.py --host 127.0.0.1     # from the host instead of the network

Inside the compose network the edges are reached by service name; from the host use
--host 127.0.0.1 (the ports are published). UDP can drop, so --repeat resends.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


@dataclass(frozen=True)
class Route:
    host: str  # compose service name
    port: int
    proto: str  # "udp" | "tcp"
    header: str | None  # syslog header prefix to add, or None to send the line as is


ROUTES: dict[str, Route] = {
    "linux_sshd.log": Route("edge-core", 5524, "udp", None),
    "acme_ngfw_cef.log": Route("edge-dmz", 5515, "tcp", "<134>Sep 26 14:05:00 fw-dmz-01 "),
    "ot_historian.log": Route("edge-dmz", 5514, "udp", None),
    "garbage.bin": Route("edge-dmz", 5514, "udp", None),
}
GARBAGE_CHUNK = 1200  # bytes per datagram for the binary file


def lines_of(name: str) -> list[bytes]:
    path = CORPUS / name
    if not path.exists():
        raise SystemExit(f"no such corpus file: {path}")
    data = path.read_bytes()
    if name.endswith(".bin"):
        return [
            data[i : i + GARBAGE_CHUNK]
            for i in range(0, min(len(data), 12 * GARBAGE_CHUNK), GARBAGE_CHUNK)
        ]
    return [line for line in data.splitlines() if line.strip()]


def send(name: str, *, host_override: str | None, repeat: int, gap: float) -> int:
    route = ROUTES[name]
    host = host_override or route.host
    payloads = [(route.header.encode() + line) if route.header else line for line in lines_of(name)]
    sent = 0
    if route.proto == "udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for _ in range(repeat):
                for payload in payloads:
                    sock.sendto(payload, (host, route.port))
                    sent += 1
                    time.sleep(gap)
        finally:
            sock.close()
    else:
        with socket.create_connection((host, route.port), timeout=10) as sock:
            for _ in range(repeat):
                for payload in payloads:
                    sock.sendall(payload + b"\n")
                    sent += 1
                    time.sleep(gap)
    print(f"sent {sent:>4} x {name:<20} {route.proto.upper()} -> {host}:{route.port}")
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--file", action="append", dest="files", choices=sorted(ROUTES))
    parser.add_argument("--host", help="send to this host instead of the compose service names")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--gap", type=float, default=0.005, help="seconds between lines")
    args = parser.parse_args(argv)
    total = 0
    for name in args.files or list(ROUTES):
        try:
            total += send(name, host_override=args.host, repeat=args.repeat, gap=args.gap)
        except OSError as exc:
            print(f"failed to send {name}: {exc}", file=sys.stderr)
            return 1
    print(f"total {total} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
