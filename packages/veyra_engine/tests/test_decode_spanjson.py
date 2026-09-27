"""The two foundations: decoding with honest byte offsets, and span-tracking JSON.

Every byte offset the engine ever reports comes through these two modules, so the property
that matters most is simple: **a recorded span must slice out of the raw bytes exactly the
value it claims** (P4). Almost every test here asserts that directly.
"""

from __future__ import annotations

import json

import pytest

from veyra_engine.decode import decode
from veyra_engine.spanjson import SpanJsonError, looks_like_json, scan

HINDI = "टर्बाइन-1 दबाव"


# ---------------------------------------------------------------- decode
def test_ascii_offsets_are_identity() -> None:
    decoded = decode(b"hello world")
    assert decoded.encoding == "utf-8"
    assert decoded.invalid_bytes == 0
    assert decoded.byte_span((0, 5)) == (0, 5)
    assert decoded.slice_bytes((6, 11)) == b"world"


def test_multibyte_offsets_are_bytes_not_characters() -> None:
    raw = f"tag={HINDI};ok".encode()
    decoded = decode(raw)
    assert decoded.text == f"tag={HINDI};ok"
    start = decoded.text.index(HINDI)
    span = (start, start + len(HINDI))
    assert decoded.slice_bytes(span).decode() == HINDI
    byte_span = decoded.byte_span(span)
    assert byte_span is not None
    assert byte_span[1] - byte_span[0] == len(HINDI.encode()), "span must be byte-sized"
    assert byte_span[1] - byte_span[0] > len(HINDI), "and bytes must exceed characters here"


def test_every_char_span_slices_back() -> None:
    """Exhaustive over a mixed-script line: each single character round-trips."""
    raw = f"a{HINDI}b;1\u00e9".encode()
    decoded = decode(raw)
    for index, char in enumerate(decoded.text):
        assert decoded.slice_bytes((index, index + 1)).decode() == char


def test_latin1_is_detected_and_offsets_stay_exact() -> None:
    raw = "café; user=rené".encode("latin-1")
    decoded = decode(raw)
    assert "caf" in decoded.text
    assert not decoded.lossy
    index = decoded.text.index("user=")
    assert decoded.slice_bytes((index, index + 5)) == b"user="


def test_invalid_bytes_never_raise_and_are_counted() -> None:
    decoded = decode(b"\xff\xfe\x00bad bytes here")
    assert isinstance(decoded.text, str)
    if decoded.lossy:
        assert decoded.invalid_bytes >= 1
        # A lossy decode must refuse to hand out byte spans rather than lie about them.
        assert decoded.byte_span((0, 1)) is None


def test_empty_input() -> None:
    decoded = decode(b"")
    assert decoded.text == ""
    assert decoded.byte_span((0, 0)) == (0, 0)


def test_out_of_range_span_is_none_not_an_exception() -> None:
    decoded = decode(b"short")
    assert decoded.byte_span((0, 99)) is None
    assert decoded.byte_span((3, 1)) is None


# ---------------------------------------------------------------- spanjson
def test_object_scalars_get_spans_that_slice_back() -> None:
    text = '{"evt":"auth","msg":"user=r.patil OK","n":42,"ok":true,"nil":null}'
    result = scan(text)
    assert result.value == {
        "evt": "auth",
        "msg": "user=r.patil OK",
        "n": 42,
        "ok": True,
        "nil": None,
    }
    for path, expected in (
        ("evt", "auth"),
        ("msg", "user=r.patil OK"),
        ("n", "42"),
        ("ok", "true"),
        ("nil", "null"),
    ):
        start, end = result.spans[path]
        assert text[start:end] == expected, path


def test_string_span_excludes_quotes_but_keeps_escapes() -> None:
    text = r'{"msg":"a\"quoted\" path C:\\tmp"}'
    result = scan(text)
    assert result.value["msg"] == 'a"quoted" path C:\\tmp'
    start, end = result.spans["msg"]
    # The span covers the raw, still-escaped content — what a human sees in the raw event.
    assert text[start:end] == r"a\"quoted\" path C:\\tmp"


def test_nested_paths_and_arrays() -> None:
    text = '{"a":{"b":[{"id":7},{"id":8}]},"t":"x"}'
    result = scan(text)
    assert result.value["a"]["b"][1]["id"] == 8
    assert text[slice(*result.spans["a.b[1].id"])] == "8"
    assert text[slice(*result.spans["a.b[0].id"])] == "7"
    assert text[slice(*result.spans["t"])] == "x"


def test_path_prefix_matches_what_contracts_reference() -> None:
    result = scan('{"msg":"hello"}', path_prefix="json")
    assert "json.msg" in result.spans


def test_trailing_text_becomes_rest_with_its_own_span() -> None:
    """Real logs append things after the JSON body; that is data, not an error."""
    text = '{"evt":"auth"} | trace=  at com.x.Auth.login(Auth.java:88)'
    result = scan(text)
    assert result.value == {"evt": "auth"}
    assert result.rest == "| trace=  at com.x.Auth.login(Auth.java:88)"
    assert result.rest_span is not None
    assert text[slice(*result.rest_span)] == result.rest


def test_scan_from_an_offset_reports_spans_in_the_original_coordinates() -> None:
    text = '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"hi"}'
    body = text.index("{")
    result = scan(text, body)
    assert result.value["msg"] == "hi"
    start, end = result.spans["msg"]
    assert text[start:end] == "hi", "spans must be absolute, not relative to the body"


def test_unicode_escapes_including_surrogate_pairs() -> None:
    text = r'{"a":"\u0915\u093e","b":"\ud83d\ude00"}'
    result = scan(text)
    assert result.value["a"] == "का"
    assert result.value["b"] == "\U0001f600"


def test_matches_stdlib_json_for_valid_documents() -> None:
    for text in (
        '{"a":1,"b":[1,2,{"c":"d"}],"e":null}',
        "[1,2,3]",
        '{"nested":{"deep":{"deeper":[true,false]}}}',
        '{"num":-12.5,"exp":1e3}',
        '{"empty_obj":{},"empty_arr":[]}',
        '{"unicode":"héllo","escaped":"tab\\there"}',
    ):
        assert scan(text).value == json.loads(text), text


@pytest.mark.parametrize(
    "bad",
    ['{"a":', "{", "[", '{"a" 1}', '{"a":}', "", "   ", '{"a":tru}', '{"a":"unterminated'],
)
def test_malformed_json_raises_a_typed_error(bad: str) -> None:
    with pytest.raises(SpanJsonError):
        scan(bad)


def test_looks_like_json() -> None:
    assert looks_like_json('  {"a":1}')
    assert looks_like_json("[1]")
    assert not looks_like_json("<134>Sep 26 hello")
    assert not looks_like_json("")


def test_spans_survive_the_trip_to_bytes() -> None:
    """The combination that matters: JSON span -> byte span -> the original bytes."""
    payload = f'{{"evt":"auth","tag":"{HINDI}","msg":"user=r.patil OK"}}'
    raw = payload.encode()
    decoded = decode(raw)
    result = scan(decoded.text)
    for path, expected in (("tag", HINDI), ("msg", "user=r.patil OK"), ("evt", "auth")):
        byte_span = decoded.byte_span(result.spans[path])
        assert byte_span is not None
        assert raw[byte_span[0] : byte_span[1]].decode() == expected, path
