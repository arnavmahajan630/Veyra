"""Unit tests for the engine's pieces: peel layers, template compiler, mapping, time, validate.

Each of these is a place where a subtle mistake would show up far away — a wrong span in a peel
layer becomes a wrong byte offset in the console; a lenient template regex becomes a false tier
1; a clock read in time parsing becomes a non-deterministic pipeline. So they get pinned here,
close to the code.
"""

from __future__ import annotations

import pytest

from veyra_engine.decode import decode
from veyra_engine.mapping import apply_map, build_observables, coerce, set_path
from veyra_engine.mask import mask
from veyra_engine.peel import (
    Region,
    detect_cef,
    detect_csv,
    detect_kv,
    detect_syslog,
    peel_cef,
    peel_csv,
    peel_json,
    peel_kv,
    peel_leef,
    peel_regex,
    peel_syslog,
    run_layers,
)
from veyra_engine.template import TemplateError, compile_pattern, compile_template, match_templates
from veyra_engine.testing import CompileError, mini_compile
from veyra_engine.timeparse import parse_time, to_epoch_ms
from veyra_engine.validate import ocsf_version, supported_classes, validate_event

RECEIVED = "2026-09-26T14:10:00.000000000Z"


def region(text: str) -> Region:
    return Region(text=text, span=(0, len(text)))


# ---------------------------------------------------------------- peel: syslog
def test_syslog_3164_fields_and_spans() -> None:
    text = "<134>Sep 26 14:05:11 fw01 app[233]: hello world"
    result = peel_syslog(region(text))
    fields = {f.path: f for f in result.fields}
    assert fields["syslog.host"].value == "fw01"
    assert text[slice(*fields["syslog.host"].char_span)] == "fw01"
    assert fields["syslog.app"].value == "app"
    assert fields["syslog.pid"].value == "233"
    assert fields["syslog.timestamp"].value == "Sep 26 14:05:11"
    # pri 134 -> facility 16, severity 6; arithmetic, so no span.
    assert fields["syslog.facility"].value == 16
    assert fields["syslog.severity"].value == 6
    assert fields["syslog.facility"].char_span is None
    assert result.body is not None
    assert result.body.text == "hello world"


def test_syslog_5424_is_preferred_when_it_applies() -> None:
    text = '<165>1 2026-09-26T14:05:11.003Z fw01 evntslog 1234 ID47 [x@1 a="b"] the message'
    assert detect_syslog(region(text)) == "rfc5424"
    result = peel_syslog(region(text))
    fields = {f.path: f.value for f in result.fields}
    assert fields["syslog.version"] == "1"
    assert fields["syslog.host"] == "fw01"
    assert fields["syslog.app"] == "evntslog"
    assert fields["syslog.msgid"] == "ID47"
    assert result.body is not None and result.body.text == "the message"


def test_syslog_without_priority_still_parses() -> None:
    text = "Sep 26 14:05:11 host01 app: body here"
    assert detect_syslog(region(text)) == "rfc3164"
    result = peel_syslog(region(text))
    assert {f.path for f in result.fields} >= {"syslog.timestamp", "syslog.host"}


def test_syslog_multiline_body_is_kept_whole() -> None:
    text = "<134>Sep 26 14:05:11 fw01 app[233]: first line\n  at com.x.Y(Y.java:1)"
    result = peel_syslog(region(text))
    assert result.body is not None
    assert "\n  at com.x.Y" in result.body.text, "the continuation must stay in the body"


def test_not_syslog_reports_an_error_rather_than_guessing() -> None:
    assert peel_syslog(region("just some text")).error == "not syslog"
    assert detect_syslog(region("just some text")) is None


# ---------------------------------------------------------------- peel: json / kv
def test_json_layer_selects_the_text_field() -> None:
    text = '{"evt":"auth","msg":"user=x OK"} | trace='
    result = peel_json(region(text), text_field="msg")
    assert result.text_field is not None
    assert result.text_field.text == "user=x OK"
    paths = {f.path for f in result.fields}
    assert {"json.evt", "json.msg", "json.__rest"} <= paths
    rest = next(f for f in result.fields if f.path == "json.__rest")
    assert rest.value == "| trace="


def test_json_layer_rejects_non_json_without_raising() -> None:
    assert peel_json(region("not json at all")).error is not None
    assert peel_json(region('{"broken":')).error is not None


def test_kv_layer_unquotes_values_but_spans_the_raw() -> None:
    text = 'src=10.0.0.1 msg="hello world" act=deny'
    result = peel_kv(region(text))
    fields = {f.path: f for f in result.fields}
    assert fields["kv.src"].value == "10.0.0.1"
    assert fields["kv.msg"].value == "hello world"
    assert text[slice(*fields["kv.msg"].char_span)] == "hello world", "span excludes the quotes"
    assert fields["kv.act"].value == "deny"


def test_kv_detection_needs_density_not_just_one_pair() -> None:
    assert detect_kv(region("a=1 b=2 c=3"))
    assert not detect_kv(region("a long sentence with one=pair inside of it and more words"))


# ---------------------------------------------------------------- peel: cef / leef / csv
def test_cef_header_and_extension() -> None:
    text = "CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22 act=deny"
    assert detect_cef(region(text))
    result = peel_cef(region(text))
    fields = {f.path: f for f in result.fields}
    assert fields["cef.device_vendor"].value == "Acme"
    assert fields["cef.device_product"].value == "NGFW"
    assert fields["cef.signature_id"].value == "100"
    assert fields["cef.name"].value == "traffic deny"
    assert fields["cef.severity"].value == "5"
    assert fields["cef.src"].value == "45.12.3.9"
    assert text[slice(*fields["cef.src"].char_span)] == "45.12.3.9"
    # Templates match on the human-readable name.
    assert result.text_field is not None and result.text_field.text == "traffic deny"


def test_leef_header_and_attributes() -> None:
    text = "LEEF:1.0|Acme|NGFW|9.1|100|src=1.2.3.4\tdst=5.6.7.8"
    result = peel_leef(region(text))
    fields = {f.path: f.value for f in result.fields}
    assert fields["leef.vendor"] == "Acme"
    assert fields["leef.event_id"] == "100"
    assert fields["leef.src"] == "1.2.3.4"


def test_csv_layer_names_columns_from_the_header() -> None:
    text = "26-09-2026 14:05:00;HIST01;TAG0001;OK;12.4"
    assert detect_csv(region(text)) == ";"
    result = peel_csv(region(text), ";", ["ts", "device", "tag", "state", "value"])
    fields = {f.path: f for f in result.fields}
    assert fields["csv.tag"].value == "TAG0001"
    assert text[slice(*fields["csv.value"].char_span)] == "12.4"


def test_regex_layer_exposes_named_groups() -> None:
    result = peel_regex(region("id=42 name=bob"), r"id=(?P<id>\d+)\s+name=(?P<name>\w+)")
    fields = {f.path: f.value for f in result.fields}
    assert fields["regex.id"] == "42"
    assert fields["regex.name"] == "bob"


def test_regex_layer_reports_a_bad_pattern_instead_of_raising() -> None:
    assert peel_regex(region("x"), "([unclosed").error is not None


# ---------------------------------------------------------------- peel: orchestration
def test_run_layers_applies_the_declared_order() -> None:
    text = '<134>Sep 26 14:05:11 fw01 app[233]: {"evt":"auth","msg":"user=r.patil OK"} | trace='
    outcome = run_layers(text, [{"syslog": {"variant": "auto"}}, {"json": {"text_field": "msg"}}])
    assert outcome.layers == ["syslog", "json"]
    assert outcome.text_field is not None
    assert outcome.text_field.text == "user=r.patil OK"
    # The text field's span must be absolute, so offsets land in the original bytes.
    start, end = outcome.text_field.span
    assert text[start:end] == "user=r.patil OK"


def test_run_layers_stops_and_explains_when_a_layer_does_not_apply() -> None:
    outcome = run_layers("plain text", [{"syslog": {}}, {"json": {}}])
    assert outcome.layers == []
    assert outcome.error is not None and "syslog" in outcome.error


def test_run_layers_respects_the_depth_cap() -> None:
    specs = [{"syslog": {}}, {"json": {}}, {"kv": {}}, {"kv": {}}, {"kv": {}}]
    outcome = run_layers("<134>Sep 26 14:05:11 h a: x=1 y=2", specs, max_depth=2)
    assert outcome.depth <= 2
    assert outcome.error is not None and "depth" in outcome.error


def test_unknown_layer_is_reported() -> None:
    outcome = run_layers("x", [{"protobuf": {}}])
    assert outcome.error is not None and "unknown envelope layer" in outcome.error


# ---------------------------------------------------------------- template compiler
def test_pattern_compiles_to_an_anchored_regex_with_named_groups() -> None:
    regex, captures = compile_pattern("user=<user> FAILED login from <src_ip:ip> attempts:<n:int>")
    assert regex.startswith("^") and regex.endswith("$")
    assert [c.name for c in captures] == ["user", "src_ip", "n"]
    assert [c.type for c in captures] == ["token", "ip", "int"]


def test_literal_text_is_escaped_and_whitespace_is_flexible() -> None:
    template = compile_template({"id": "t", "pattern": "cost: <amount> (usd)"})
    assert template.pattern.match("cost:   12 (usd)"), "runs of whitespace must match"
    assert not template.pattern.match("cost: 12 usd"), "literal parens must be required"


@pytest.mark.parametrize(
    ("pattern", "text", "should_match"),
    [
        ("a <x:int> b", "a 42 b", True),
        ("a <x:int> b", "a notanint b", False),
        ("ip <x:ip>", "ip 10.0.0.1", True),
        ("ip <x:ip>", "ip 10.0.0.1.9.9", False),
        ("q <x:quoted>", 'q "hello world"', True),
        ("w <x:word>", "w host.name-1", True),
        ("r <x:rest>", "r everything after here", True),
        ("anon <*> tail", "anon whatever tail", True),
    ],
)
def test_capture_types(pattern: str, text: str, should_match: bool) -> None:
    template = compile_template({"id": "t", "pattern": pattern})
    assert bool(template.pattern.match(text)) is should_match


def test_first_matching_template_wins() -> None:
    specific = compile_template(
        {"id": "specific", "pattern": "Failed password for invalid user <u> from <ip:ip>"}
    )
    general = compile_template({"id": "general", "pattern": "Failed password for <u> from <ip:ip>"})
    text = "Failed password for invalid user admin from 45.12.3.9"
    assert match_templates([specific, general], text).template.id == "specific"
    # Order is the contract author's decision: reversed, the general one cannot match this text
    # because <u> is a single token, so the specific one still wins.
    assert match_templates([general, specific], text).template.id == "specific"


def test_capture_spans_are_shifted_by_the_text_field_offset() -> None:
    template = compile_template({"id": "t", "pattern": "user=<user> ok"})
    matched = match_templates([template], "user=bob ok", offset=100)
    assert matched is not None
    assert matched.spans["user"] == (105, 108)


@pytest.mark.parametrize(
    "bad",
    ["user=<unclosed", "<:ip>", "<a><a>", "<x:nosuchtype>"],
)
def test_bad_patterns_raise_at_compile_time(bad: str) -> None:
    with pytest.raises(TemplateError):
        compile_pattern(bad)


def test_precompiled_regex_is_used_as_is() -> None:
    template = compile_template(
        {"id": "t", "regex": r"^x=(?P<x>\d+)$", "captures": [{"name": "x", "type": "int"}]}
    )
    assert match_templates([template], "x=9").values == {"x": "9"}


# ---------------------------------------------------------------- mapping
def test_set_path_builds_nested_objects() -> None:
    target: dict = {}
    set_path(target, "src_endpoint.ip", "1.2.3.4")
    set_path(target, "src_endpoint.port", 22)
    assert target == {"src_endpoint": {"ip": "1.2.3.4", "port": 22}}


@pytest.mark.parametrize(
    ("path", "value", "expected", "has_error"),
    [
        ("src_endpoint.ip", "10.0.0.1", "10.0.0.1", False),
        ("src_endpoint.ip", "not-an-ip", None, True),
        ("src_endpoint.ip", "::1", "::1", False),
        ("src_endpoint.port", "443", 443, False),
        ("src_endpoint.port", "http", None, True),
        ("severity_id", "3", 3, False),
        ("message", 42, "42", False),
    ],
)
def test_coercion_types_values_and_reports_failures(
    path: str, value: object, expected: object, has_error: bool
) -> None:
    got, error = coerce(path, value)
    assert got == expected
    assert bool(error) is has_error


def test_apply_map_records_offsets_for_located_values_and_derived_for_the_rest() -> None:
    raw = b"user=bob from 10.0.0.1"
    decoded = decode(raw)
    captures = {"user": "bob", "ip": "10.0.0.1"}
    spans = {"user": (5, 8), "ip": (14, 22)}
    entries = [
        {"ocsf_path": "user.name", "kind": "capture", "ref": "user"},
        {"ocsf_path": "src_endpoint.ip", "kind": "capture", "ref": "ip"},
        {"ocsf_path": "status_id", "kind": "const", "value": 1},
        {"ocsf_path": "message", "kind": "text"},
    ]
    result = apply_map(
        entries,
        captures=captures,
        capture_spans=spans,
        peeled={},
        text=decoded.text,
        text_span=(0, len(decoded.text)),
        decoded=decoded,
    )
    assert result.ocsf["user"]["name"] == "bob"
    assert result.field_offsets["user.name"] == (5, 8)
    assert raw[slice(*result.field_offsets["src_endpoint.ip"])] == b"10.0.0.1"
    # A constant is not in the raw bytes, so it must be declared derived, never located.
    assert "status_id" not in result.field_offsets
    assert result.derived_fields["status_id"] == "const"


def test_apply_map_reports_missing_references_instead_of_inventing_values() -> None:
    decoded = decode(b"nothing here")
    result = apply_map(
        [{"ocsf_path": "user.name", "kind": "capture", "ref": "absent"}],
        captures={},
        capture_spans={},
        peeled={},
        text=decoded.text,
        text_span=(0, 12),
        decoded=decoded,
    )
    assert result.missing == ["user.name"]
    assert "user" not in result.ocsf


def test_vocab_lookup_is_case_insensitive_and_marked_derived() -> None:
    decoded = decode(b"status=FAILED")
    result = apply_map(
        [{"ocsf_path": "status_id", "kind": "vocab", "vocab": "status_words", "ref": "status"}],
        captures={"status": "failed"},
        capture_spans={"status": (7, 13)},
        peeled={},
        text=decoded.text,
        text_span=(0, 13),
        decoded=decoded,
        vocab={"status_words": {"entries": {"FAILED": {"status_id": 2}, "OK": {"status_id": 1}}}},
    )
    assert result.ocsf["status_id"] == 2
    assert result.derived_fields["status_id"] == "vocab:status_words"


def test_observables_come_from_typed_fields() -> None:
    observables = build_observables(
        {
            "src_endpoint": {"ip": "1.2.3.4"},
            "dst_endpoint": {"ip": "5.6.7.8"},
            "user": {"name": "bob"},
            "device": {"hostname": "host1"},
        }
    )
    kinds = {(o["type_id"], o["value"]) for o in observables}
    assert (2, "1.2.3.4") in kinds
    assert (2, "5.6.7.8") in kinds
    assert (4, "bob") in kinds
    assert (1, "host1") in kinds


# ---------------------------------------------------------------- time
def test_time_uses_the_envelope_not_the_clock() -> None:
    """P3: two calls with the same inputs must agree, whatever the wall clock says."""
    first = parse_time("Sep 26 14:05:11", received_time=RECEIVED, formats=["%b %d %H:%M:%S"])
    second = parse_time("Sep 26 14:05:11", received_time=RECEIVED, formats=["%b %d %H:%M:%S"])
    assert first.epoch_ms == second.epoch_ms
    assert first.source == "event"
    assert first.year_inferred is True


def test_year_inference_rolls_back_across_new_year() -> None:
    """A December event read in January belongs to the previous year."""
    result = parse_time(
        "Dec 31 23:59:00",
        received_time="2027-01-01T00:05:00.000000000Z",
        formats=["%b %d %H:%M:%S"],
    )
    assert result.year_inferred
    from datetime import UTC, datetime

    assert datetime.fromtimestamp(result.epoch_ms / 1000, UTC).year == 2026


def test_timezone_is_applied_and_recorded() -> None:
    utc = parse_time("2026-09-26 14:05:11", received_time=RECEIVED)
    ist = parse_time("2026-09-26 14:05:11", received_time=RECEIVED, tz="Asia/Kolkata")
    assert utc.epoch_ms - ist.epoch_ms == int(5.5 * 3600 * 1000)
    assert ist.tz_assumed == "Asia/Kolkata"


def test_explicit_offset_in_the_timestamp_wins_over_the_contract_timezone() -> None:
    result = parse_time("2026-09-26T14:05:11+05:30", received_time=RECEIVED, tz="UTC")
    assert result.tz_assumed is None, "the event carried its own offset"


def test_unparseable_time_falls_back_to_arrival() -> None:
    result = parse_time("not a time", received_time=RECEIVED)
    assert result.source == "received"
    assert result.epoch_ms == to_epoch_ms(
        __import__("veyra_engine.timeparse", fromlist=["parse_received"]).parse_received(RECEIVED)
    )


def test_missing_time_falls_back_to_arrival() -> None:
    assert parse_time(None, received_time=RECEIVED).source == "received"
    assert parse_time("   ", received_time=RECEIVED).source == "received"


def test_clock_skew_is_recorded() -> None:
    result = parse_time("2026-09-26 14:09:00", received_time=RECEIVED)
    assert result.clock_skew_ms == 60_000, "arrival was one minute after the event"


# ---------------------------------------------------------------- validate
def test_schema_version_and_classes_are_the_pinned_subset() -> None:
    assert ocsf_version() == "1.9.0"
    assert supported_classes() == (0, 1007, 3002, 4001, 4002)


def base_event(**over: object) -> dict:
    event = {
        "class_uid": 3002,
        "category_uid": 3,
        "type_uid": 300201,
        "activity_id": 1,
        "severity_id": 1,
        "time": 1790000000000,
        "raw_data": "x",
    }
    event.update(over)
    return event


def test_a_good_event_validates() -> None:
    assert validate_event(base_event()).ok


def test_a_bad_enum_is_caught() -> None:
    result = validate_event(base_event(severity_id=42))
    assert not result.ok
    assert any("severity_id" in error for error in result.errors)


def test_a_wrong_class_activity_pair_is_caught() -> None:
    # 1007 Process Activity has no activity 7.
    result = validate_event(base_event(class_uid=1007, category_uid=1, activity_id=7))
    assert not result.ok


def test_a_string_where_an_int_belongs_is_caught() -> None:
    assert not validate_event(base_event(time="yesterday")).ok


def test_contract_required_paths_are_enforced() -> None:
    result = validate_event(base_event(), ["user.name"])
    assert not result.ok
    assert result.missing_required == ["user.name"]
    ok = validate_event(base_event(user={"name": "bob"}), ["user.name"])
    assert ok.ok


def test_unknown_class_falls_back_to_base_event_rather_than_failing_open() -> None:
    result = validate_event(base_event(class_uid=9999, category_uid=0))
    assert not result.ok, "class_uid 9999 is not the Base Event const, so it must be rejected"


# ---------------------------------------------------------------- mask
def test_mask_hides_secrets_and_emails_but_keeps_ips() -> None:
    masked, mapping = mask("user=bob password=hunter2 mail=bob@example.com from 10.0.0.1")
    assert "hunter2" not in masked
    assert "bob@example.com" not in masked
    assert "10.0.0.1" in masked, "IPs are needed for mapping and are not secrets"
    assert mapping


def test_mask_is_deterministic() -> None:
    text = "token=abc123 user=x@y.zz"
    assert mask(text)[0] == mask(text)[0]


# ---------------------------------------------------------------- mini_compile
def test_mini_compile_produces_the_compiled_shape() -> None:
    compiled = mini_compile(
        """
contract: demo
version: 3
tenant: t_x
sources: [src_x]
envelope:
  - syslog: {variant: auto}
templates:
  - id: t1
    pattern: 'user=<user> in'
    class: authentication
    activity: logon
    map:
      user.name: $user
      status_id: {const: 1}
      time: {ts: $syslog.timestamp, formats: ["%b %d %H:%M:%S"]}
      severity_id: {vocab: status_words, from: $user}
      message: $__text
      device.hostname: $syslog.host
required: [time]
"""
    )
    assert compiled["contract"] == "demo"
    assert compiled["version"] == 3
    template = compiled["templates"][0]
    assert template["class_uid"] == 3002
    assert template["activity_id"] == 1
    assert template["type_uid"] == 300201, "type_uid = class_uid * 100 + activity_id"
    assert template["category"] == "iam"
    kinds = {entry["ocsf_path"]: entry["kind"] for entry in template["map"]}
    assert kinds == {
        "user.name": "capture",
        "status_id": "const",
        "time": "ts",
        "severity_id": "vocab",
        "message": "text",
        "device.hostname": "field",
    }


@pytest.mark.parametrize(
    "bad",
    [
        "not: a contract",
        "contract: x\ntemplates:\n  - id: t\n",  # no pattern
        "contract: x\ntemplates:\n  - pattern: 'a'\n",  # no id
        "contract: x\ntemplates:\n  - {id: t, pattern: 'a', class: nope}",
        "contract: x\ntemplates:\n  - {id: t, pattern: 'a', class: authentication, activity: fly}",
    ],
)
def test_mini_compile_rejects_bad_contracts_loudly(bad: str) -> None:
    with pytest.raises(CompileError):
        mini_compile(bad)
