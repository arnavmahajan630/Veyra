"""Framing and stamping: the shared rule the edge and the gateway both obey."""

from __future__ import annotations

import base64

from veyra_common.envelope import rfc3339_ns, stamp, stamp_framed
from veyra_common.framing import frame_datagram, is_continuation, split_lines
from veyra_common.hashing import sha256_hex

T3 = (
    b'<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=a.sharma FAILED login '
    b'from 103.21.4.77 via 10.2.3.4 attempts:1"} | trace=\n  at com.x.Auth.login(Auth.java:88)'
)


def test_continuation_rule() -> None:
    assert is_continuation(b"  at com.x.Auth.login(Auth.java:88)")
    assert is_continuation(b"\tmore context")
    assert is_continuation(b"at com.x.Auth.login(Auth.java:88)")
    assert not is_continuation(b"<134>Sep 26 14:05:11 fw01 app[233]: hello")
    assert not is_continuation(b"")


def test_multiline_join_keeps_original_bytes() -> None:
    events = split_lines(T3)
    assert len(events) == 1
    ev = events[0]
    assert ev.parts == 2
    assert ev.method == "multiline_join"
    assert ev.raw == T3, "joined bytes must equal the original lines joined with \\n"


def test_separate_events_stay_separate() -> None:
    data = b"line one\nline two\nline three"
    events = split_lines(data)
    assert [e.raw for e in events] == [b"line one", b"line two", b"line three"]
    assert all(e.method == "newline" and e.parts == 1 for e in events)


def test_blank_lines_are_dropped_and_crlf_stripped() -> None:
    events = split_lines(b"a\r\n\r\nb\r\n")
    assert [e.raw for e in events] == [b"a", b"b"]


def test_truncation_flags() -> None:
    events = split_lines(b"a" * 100, max_event_bytes=10)
    assert events[0].truncated and len(events[0].raw) == 10
    dgram = frame_datagram(b"b" * 100, max_event_bytes=10)
    assert dgram.truncated and dgram.method == "datagram"


def test_stamp_is_evidence_grade() -> None:
    env = stamp(
        T3,
        collector_id="edge-dmz-01",
        transport="syslog_tcp",
        framing_method="multiline_join",
        parts=2,
        source_id="src_authsrv_01",
        tenant_id="t_maha_power",
        vendor="custom",
    )
    assert env.raw_len == len(T3)
    assert env.raw_sha256 == sha256_hex(T3)
    assert base64.b64decode(env.raw_b64) == T3
    assert env.hash_matches()
    assert env.framing.parts == 2


def test_stamp_framed_carries_framing() -> None:
    ev = split_lines(T3)[0]
    env = stamp_framed(
        ev, collector_id="edge-dmz-01", transport="syslog_tcp", source_id="src_authsrv_01"
    )
    assert env.framing.method == "multiline_join"
    assert env.framing.parts == 2
    assert env.raw_bytes == T3


def test_stamp_truncates_and_hashes_stored_bytes() -> None:
    env = stamp(
        b"x" * 50,
        collector_id="c",
        transport="syslog_udp",
        framing_method="datagram",
        max_event_bytes=10,
    )
    assert env.framing.truncated
    assert env.raw_len == 10
    assert env.hash_matches(), "the hash must cover what was stored, so verify still passes"


def test_unregistered_defaults() -> None:
    env = stamp(
        b"hello", collector_id="edge-core-01", transport="syslog_udp", framing_method="datagram"
    )
    assert (env.tenant_id, env.source_id, env.vendor) == (
        "unassigned",
        "unregistered",
        "unregistered",
    )
    assert env.auth.method == "none"


def test_received_time_has_nanosecond_precision() -> None:
    ts = rfc3339_ns(1790000000_123456789)
    assert ts == "2026-09-21T14:13:20.123456789Z"
    env = stamp(b"x", collector_id="c", transport="kafka", framing_method="http_body")
    assert env.received_time.endswith("Z") and len(env.received_time) == 30
