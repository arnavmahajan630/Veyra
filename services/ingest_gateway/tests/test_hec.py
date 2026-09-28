"""The HEC body parser — the protocol quirk A2 lists as its main risk.

Real HEC clients concatenate JSON objects with no separator, so this is where the gateway is most
likely to silently lose events: a splitter that gets confused by a brace inside a string would drop
everything after it, and the client would still see a 200.
"""

from __future__ import annotations

import json

import pytest
from ingest_gateway.hec import HecFormatError, event_from_object, events_from_body, split_objects


def test_one_object() -> None:
    assert split_objects('{"event":"hello"}') == [{"event": "hello"}]


def test_concatenated_objects_without_separators() -> None:
    """The quirk: valid HEC, invalid JSON."""
    body = '{"event":"one"}{"event":"two"}{"event":"three"}'
    assert [o["event"] for o in split_objects(body)] == ["one", "two", "three"]


def test_newline_separated_objects() -> None:
    """What Vector's splunk_hec_logs sink sends — the same rule covers it."""
    body = '{"event":"one"}\n{"event":"two"}\n'
    assert [o["event"] for o in split_objects(body)] == ["one", "two"]


def test_braces_inside_strings_do_not_split() -> None:
    """The case brace counting gets wrong."""
    body = '{"event":"a } b { c"}{"event":"second"}'
    events = [o["event"] for o in split_objects(body)]
    assert events == ["a } b { c", "second"]


def test_escaped_quotes_inside_strings() -> None:
    body = '{"event":"he said \\"hi\\" }"}{"event":"next"}'
    assert [o["event"] for o in split_objects(body)] == ['he said "hi" }', "next"]


def test_nested_objects() -> None:
    body = '{"event":{"a":{"b":[1,2]}},"host":"h"}{"event":"x"}'
    objects = split_objects(body)
    assert objects[0]["event"]["a"]["b"] == [1, 2]
    assert objects[1]["event"] == "x"


def test_trailing_garbage_is_an_error_not_a_silent_truncation() -> None:
    with pytest.raises(HecFormatError, match="after 1 object"):
        split_objects('{"event":"one"} not json at all')


def test_a_bare_array_is_rejected() -> None:
    with pytest.raises(HecFormatError, match="expected a JSON object"):
        split_objects('[{"event":"one"}]')


def test_empty_body() -> None:
    with pytest.raises(HecFormatError, match="empty body"):
        split_objects("   \n  ")


def test_string_event_keeps_the_senders_bytes_exactly() -> None:
    """The archive must hold the log line, not a JSON wrapper VEYRA invented."""
    line = '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth"} | trace='
    event = event_from_object({"event": line})
    assert event.raw == line.encode()
    assert event.meta is None


def test_object_event_is_compact_and_key_sorted() -> None:
    """Deterministic bytes: the same object must always hash to the same raw_sha256."""
    a = event_from_object({"event": {"b": 2, "a": 1}})
    b = event_from_object({"event": {"a": 1, "b": 2}})
    assert a.raw == b.raw == b'{"a":1,"b":2}'
    assert json.loads(a.raw) == {"a": 1, "b": 2}


def test_hec_metadata_is_captured() -> None:
    event = event_from_object(
        {
            "event": "line",
            "time": 1790000000.5,
            "host": "authsrv-01",
            "source": "/var/log/auth.log",
            "sourcetype": "linux_secure",
            "index": "main",
        }
    )
    assert event.meta is not None
    assert event.meta.time == 1790000000.5
    assert event.meta.host == "authsrv-01"
    assert event.meta.sourcetype == "linux_secure"


def test_integer_time_is_accepted() -> None:
    event = event_from_object({"event": "line", "time": 1790000000})
    assert event.meta is not None and event.meta.time == 1790000000.0


def test_non_numeric_time_is_a_format_error() -> None:
    with pytest.raises(HecFormatError, match="'time' is not a number"):
        event_from_object({"event": "line", "time": "yesterday"})


def test_missing_event_field() -> None:
    with pytest.raises(HecFormatError, match="no 'event' field"):
        event_from_object({"host": "h"})


def test_null_event() -> None:
    with pytest.raises(HecFormatError, match="'event' is null"):
        event_from_object({"event": None})


def test_unknown_fields_are_kept_as_extras() -> None:
    event = event_from_object({"event": "line", "fields": {"site": "pune"}})
    assert event.extras == {"fields": {"site": "pune"}}


def test_events_from_body_end_to_end() -> None:
    body = '{"event":"one","host":"a"}{"event":"two"}'
    events = events_from_body(body)
    assert [e.raw for e in events] == [b"one", b"two"]
    assert events[0].meta is not None and events[0].meta.host == "a"
    assert events[1].meta is None
