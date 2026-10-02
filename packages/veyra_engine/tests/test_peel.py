"""Envelope layers: an optional layer peels when it is there and is skipped when it is not.

A firewall's CEF reaches VEYRA two ways — through the syslog listener, where the collector's
header is in front of it, and over HTTP push, where it is not. Both are the same format and
one contract, so the syslog layer is declared `optional: true`. Before that, the cascade
stopped on the missing header and every syslog-framed CEF line fell to tier 3, which is why
the demo used to publish CEF straight onto `raw.acme_ngfw` instead of sending it.
"""

from __future__ import annotations

from veyra_engine.peel import run_layers


def test_an_optional_layer_that_is_absent_does_not_stop_the_cascade() -> None:
    """A firewall's CEF arrives with a syslog header over syslog and without it over HTTP."""
    bare = "CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22"
    outcome = run_layers(bare, [{"syslog": {"optional": True}}, {"cef": {}}])
    assert outcome.error is None
    # The parse path says what was really peeled, so a missing header is visible.
    assert outcome.layers == ["cef"]
    assert {f.path: f.value for f in outcome.fields}["cef.src"] == "45.12.3.9"


def test_an_optional_layer_that_is_present_is_peeled_like_any_other() -> None:
    wrapped = (
        "<134>Sep 26 14:05:00 fw01 "
        "CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4 dpt=22"
    )
    outcome = run_layers(wrapped, [{"syslog": {"optional": True}}, {"cef": {}}])
    assert outcome.error is None
    assert outcome.layers == ["syslog", "cef"]
    fields = {f.path: f.value for f in outcome.fields}
    assert fields["syslog.host"] == "fw01"
    assert fields["cef.src"] == "45.12.3.9"


def test_a_required_layer_that_is_absent_still_stops_the_cascade() -> None:
    bare = "CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9"
    outcome = run_layers(bare, [{"syslog": {}}, {"cef": {}}])
    assert outcome.error is not None and outcome.error.startswith("syslog:")
    assert outcome.layers == []


def test_an_optional_layer_keeps_the_byte_spans_of_what_follows() -> None:
    """P4: an offset must still slice the real bytes, header or no header."""
    wrapped = (
        "<134>Sep 26 14:05:00 fw01 "
        "CEF:0|Acme|NGFW|9.1|100|traffic deny|5|src=45.12.3.9 dst=10.2.3.4"
    )
    outcome = run_layers(wrapped, [{"syslog": {"optional": True}}, {"cef": {}}])
    span = next(f.char_span for f in outcome.fields if f.path == "cef.src")
    assert span is not None
    assert wrapped[span[0] : span[1]] == "45.12.3.9"
