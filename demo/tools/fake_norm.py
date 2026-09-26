"""Publish IF-NORM-EVENT fixtures to ``norm.<category>`` without a normalizer.

Lets B build the lineage indexer and the router-facing views, and C build Overview /
Sources, before A3 lands.

    python demo/tools/fake_norm.py --eps 20 --seconds 30
    python demo/tools/fake_norm.py --tier 3 --count 8

Tier 1 events are a realistic Authentication/Logon failure (class 3002 -> norm.iam);
tier 3 events are the messy authsrv line with observables and no class (-> norm.uncategorized),
exactly the shape Wazuh sees for an unknown template in Beat 3.
"""

from __future__ import annotations

import argparse
import time

from veyra_common.envelope import rfc3339_ns
from veyra_common.hashing import sha256_hex, template_sig
from veyra_common.ids import uuid7_str
from veyra_common.kafka import make_producer
from veyra_common.models import NormEvent
from veyra_common.settings import settings
from veyra_common.topics import category_for_class, norm_topic

RAW_T1 = (
    b'<134>Sep 26 14:05:09 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK login '
    b'from 10.4.1.20 via 10.2.3.4"} | trace='
)
RAW_T3 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)
TEXT_T3 = "user=a.sharma FAILED login from 103.21.4.77 via 10.2.3.4 attempts:1"


def _ulpf(event_uid: str, tier: int, offset: int, raw: bytes, now: str) -> dict:
    return {
        "v": 1,
        "event_uid": event_uid,
        "tenant_id": "t_maha_power",
        "source_id": "src_authsrv_01",
        "vendor": "custom",
        "zone": "dmz",
        "raw_ref": {"topic": "raw.custom", "partition": 0, "offset": offset},
        "raw_sha256": sha256_hex(raw),
        "received_time": now,
        "custody": "realtime",
        "contract": {"id": "authsrv", "version": 1},
        "template": {
            "sig": template_sig("authsrv", TEXT_T3),
            "id": "auth_ok" if tier == 1 else None,
        },
        "tier": tier,
        "conformance": "match" if tier == 1 else "unknown_template",
        "parse_path": ["syslog:rfc3164", "json", "template:auth_ok"]
        if tier == 1
        else ["auto:syslog3164", "auto:json", "auto:kv(rest)"],
        # Offsets into the decoded raw bytes. For the tier-1 fixture these point at the
        # real values inside the JSON string, which is what B6's highlighter draws.
        "field_offsets": {"user.name": [66, 74], "src_endpoint.ip": [86, 95]} if tier == 1 else {},
        "derived_fields": {"status_id": "const", "time": "ts:inferred_year"}
        if tier == 1
        else {"severity_id": "vocab:severity_words", "time": "ts:inferred_year"},
        "class_hint": None if tier == 1 else {"class_uid": 3002, "confidence": "medium"},
        "time": {
            "source": "event",
            "tz_assumed": "Asia/Kolkata",
            "year_inferred": True,
            "clock_skew_ms": -812,
        },
        "encoding": {"detected": "utf-8", "confidence": 0.99, "invalid_bytes": 0},
        "pii_fields": ["user.name", "src_endpoint.ip"] if tier == 1 else ["user"],
        "revision": 1,
        "supersedes": None,
        "replay": False,
        "shadow": False,
        "engine_version": "0.0.0-fake-norm",
    }


def make_event(tier: int, offset: int) -> NormEvent:
    now = rfc3339_ns()
    event_uid = uuid7_str()
    raw = RAW_T1 if tier == 1 else RAW_T3
    base = {
        "time": int(time.time() * 1000),
        "raw_data": raw.decode(),
        "metadata": {"version": "1.9.0", "product": {"name": "VEYRA", "vendor_name": "NTRO"}},
        "ulpf": _ulpf(event_uid, tier, offset, raw, now),
    }
    if tier == 1:
        return NormEvent.model_validate(
            base
            | {
                "class_uid": 3002,
                "category_uid": 3,
                "type_uid": 300201,
                "activity_id": 1,
                "severity_id": 1,
                "status_id": 1,
                "message": "user=r.patil OK login from 10.4.1.20 via 10.2.3.4",
                "user": {"name": "r.patil"},
                "src_endpoint": {"ip": "10.4.1.20"},
                "dst_endpoint": {"ip": "10.2.3.4"},
                "observables": [
                    {"name": "ip_1", "type_id": 2, "value": "10.4.1.20"},
                    {"name": "user", "type_id": 4, "value": "r.patil"},
                ],
                "unmapped": {"syslog.host": "fw01", "syslog.app": "app", "evt": "auth"},
            }
        )
    return NormEvent.model_validate(
        base
        | {
            "class_uid": 0,
            "category_uid": 0,
            "type_uid": 99,
            "activity_id": 99,
            "severity_id": 3,
            "message": TEXT_T3,
            "observables": [
                {"name": "ip_1", "type_id": 2, "value": "103.21.4.77"},
                {"name": "ip_2", "type_id": 2, "value": "10.2.3.4"},
                {"name": "user", "type_id": 4, "value": "a.sharma"},
            ],
            "unmapped": {
                "user": "a.sharma",
                "attempts": "1",
                "syslog.host": "fw01",
                "syslog.app": "app",
                "trace": "at com.x.Auth.login(Auth.java:88)",
            },
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eps", type=float, default=float(settings.demo_eps_baseline))
    parser.add_argument("--seconds", type=float, default=0.0)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--tier", type=int, choices=[1, 3], default=0, help="default: mix 70/30")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    producer = None if args.dry_run else make_producer()
    interval = 1.0 / args.eps if args.eps > 0 else 0.0
    deadline = time.monotonic() + args.seconds if args.seconds else None
    sent = 0

    while True:
        if args.count and sent >= args.count and not args.seconds:
            break
        if deadline and time.monotonic() >= deadline:
            break
        tier = args.tier or (1 if sent % 10 < 7 else 3)
        event = make_event(tier, offset=sent)
        topic = norm_topic(category_for_class(event.class_uid))
        payload = event.model_dump_json().encode()
        if producer is None:
            print(f"{topic} tier={tier} {event.ulpf.event_uid} {len(payload)}B")
        else:
            producer.produce(topic, key=event.ulpf.event_uid, value=payload)
            producer.poll(0)
        sent += 1
        if interval:
            time.sleep(interval)

    if producer is not None:
        producer.flush(15)
    print(f"sent {sent} normalized events")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
