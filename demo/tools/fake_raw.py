"""Publish valid IF-ENVELOPE records from demo/corpus/ to ``raw.<vendor>``.

This is what unblocks parallel work: B can index and archive, and C can watch the
console fill, before A1's Vector configs or A3's engine exist.

    python demo/tools/fake_raw.py --eps 50 --seconds 60
    python demo/tools/fake_raw.py --file authsrv_t3_failed.log --count 8
    python demo/tools/fake_raw.py --eps 15 --forever        # demo baseline traffic

Framing follows veyra_common.framing, so the T3 stack-trace lines arrive as ONE
envelope with ``framing.method="multiline_join"`` and ``parts=2`` — the same shape the
real edge must produce (A1 AC2).
"""

from __future__ import annotations

import argparse
import itertools
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from veyra_common.envelope import stamp
from veyra_common.framing import split_lines
from veyra_common.kafka import make_producer
from veyra_common.models import Envelope
from veyra_common.settings import settings
from veyra_common.topics import raw_topic

CORPUS = Path(__file__).resolve().parents[1] / "corpus"


# Who each corpus file pretends to be. Matches the pre-seeded world in
# docs/plan/04_DEMO_SCRIPT.md §2 and edge/vector/inventory/sources.csv.
@dataclass(frozen=True)
class SourceProfile:
    source_id: str
    tenant_id: str
    vendor: str
    zone: str
    transport: str
    collector_id: str
    listener: str
    peer_ip: str


PROFILES: dict[str, SourceProfile] = {
    "linux_sshd.log": SourceProfile(
        "src_lnx_core_07",
        "t_ntro_core",
        "linux",
        "core",
        "syslog_udp",
        "edge-core-01",
        "core-udp",
        "172.20.0.31",
    ),
    "acme_ngfw_cef.log": SourceProfile(
        "src_fw_dmz_01",
        "t_ntro_core",
        "acme_ngfw",
        "dmz",
        "syslog_tcp",
        "edge-dmz-01",
        "dmz-tcp",
        "172.20.0.21",
    ),
    "authsrv_t1_ok.log": SourceProfile(
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "dmz",
        "http_hec_event",
        "ingest-gateway-01",
        "hec",
        "172.20.0.51",
    ),
    "authsrv_t2_session.log": SourceProfile(
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "dmz",
        "http_hec_event",
        "ingest-gateway-01",
        "hec",
        "172.20.0.51",
    ),
    "authsrv_t3_failed.log": SourceProfile(
        "src_authsrv_01",
        "t_maha_power",
        "custom",
        "dmz",
        "http_hec_event",
        "ingest-gateway-01",
        "hec",
        "172.20.0.51",
    ),
    # Not in the inventory on purpose: this is what "unregistered" looks like.
    "ot_historian.log": SourceProfile(
        "unregistered",
        "unassigned",
        "unregistered",
        "core",
        "syslog_udp",
        "edge-core-01",
        "core-udp",
        "172.20.0.99",
    ),
    "garbage.bin": SourceProfile(
        "unregistered",
        "unassigned",
        "unregistered",
        "core",
        "syslog_udp",
        "edge-core-01",
        "core-udp",
        "172.20.0.98",
    ),
}


def load(files: list[str]) -> list[tuple[SourceProfile, bytes]]:
    """Read the corpus files and frame them into logical events."""
    events: list[tuple[SourceProfile, bytes]] = []
    for name in files:
        path = CORPUS / name
        if not path.exists():
            raise SystemExit(f"no such corpus file: {path} (run demo/tools/gen_corpus.py)")
        profile = PROFILES.get(name, PROFILES["garbage.bin"])
        data = path.read_bytes()
        for framed in split_lines(data, max_event_bytes=settings.max_event_bytes):
            events.append((profile, framed.raw))
    return events


def build(profile: SourceProfile, raw: bytes) -> Envelope:
    is_multiline = b"\n" in raw
    return stamp(
        raw,
        collector_id=profile.collector_id,
        transport=profile.transport,  # type: ignore[arg-type]
        framing_method="multiline_join" if is_multiline else "newline",  # type: ignore[arg-type]
        parts=raw.count(b"\n") + 1,
        zone=profile.zone,  # type: ignore[arg-type]
        tenant_id=profile.tenant_id,
        source_id=profile.source_id,
        vendor=profile.vendor,
        listener=profile.listener,
        peer_ip=profile.peer_ip,
        auth_method="api_key" if profile.transport.startswith("http") else "ip_map",
        auth_key_id="k_FAKERAW1" if profile.transport.startswith("http") else None,
        max_event_bytes=settings.max_event_bytes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eps", type=float, default=float(settings.demo_eps_baseline))
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after N seconds")
    parser.add_argument("--count", type=int, default=0, help="stop after N events")
    parser.add_argument("--forever", action="store_true")
    parser.add_argument(
        "--file",
        action="append",
        dest="files",
        help="corpus file to send (repeatable); default: every file",
    )
    parser.add_argument("--dry-run", action="store_true", help="print, do not produce")
    args = parser.parse_args()

    files = args.files or list(PROFILES)
    events = load(files)
    if not events:
        raise SystemExit("no events to send")

    limit = args.count or (0 if (args.forever or args.seconds) else len(events))
    deadline = time.monotonic() + args.seconds if args.seconds else None
    interval = 1.0 / args.eps if args.eps > 0 else 0.0

    producer = None if args.dry_run else make_producer()
    sent = 0
    errors = 0
    started = time.monotonic()

    def on_delivery(err: object, _msg: object) -> None:
        nonlocal errors
        if err is not None:
            errors += 1

    try:
        for profile, raw in itertools.cycle(events):
            if limit and sent >= limit:
                break
            if deadline and time.monotonic() >= deadline:
                break
            envelope = build(profile, raw)
            topic = raw_topic(envelope.vendor)
            if producer is None:
                print(f"{topic} {envelope.event_uid} {envelope.raw_len}B {envelope.framing.method}")
            else:
                producer.produce(
                    topic,
                    key=envelope.kafka_key(),
                    value=envelope.model_dump_json().encode(),
                    on_delivery=on_delivery,
                )
                producer.poll(0)
            sent += 1
            if interval:
                # Absolute schedule: sleep until event N is due, not "interval" from now.
                due = started + sent * interval
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
    finally:
        if producer is not None:
            producer.flush(15)

    print(f"sent {sent} envelopes from {len(files)} file(s), {errors} delivery errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
