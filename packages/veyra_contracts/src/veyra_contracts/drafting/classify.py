"""Guess the envelope layers of an unknown sample, and peel samples with given layers.

Uses A4's detectors (``detect_syslog``, ``peel_syslog``, ``peel_json``) and returns
contract-envelope layers, not ``tier3.cascade``'s parse path. Whatever it finds is
written into the drafted contract's ``envelope:``, so the engine peels runtime events
the same way: syslog (3164/5424), then CEF, LEEF, or a JSON body whose text lives in
a string field (``msg``, ``message``, ``log``, ``text``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from veyra_engine.decode import decode
from veyra_engine.peel import Region, detect_syslog, peel_json, peel_syslog, run_layers

TEXT_FIELDS = ("msg", "message", "log", "text")
Layers = list[dict[str, dict[str, Any]]]


@dataclass(frozen=True)
class Peeled:
    """One sample after peeling: the full decoded text and the template text in it."""

    raw: bytes
    text: str
    template_text: str
    template_start: int  # char offset of template_text inside text


def classify(text: str) -> Layers:
    layers: Layers = []
    region = Region(text=text, span=(0, len(text)))
    if detect_syslog(region):
        layers.append({"syslog": {"variant": "auto"}})
        body = peel_syslog(region).body
        if body is not None:
            region = body
    stripped = region.text.lstrip()
    if stripped.startswith("CEF:"):
        layers.append({"cef": {}})
    elif stripped.startswith("LEEF:"):
        layers.append({"leef": {}})
    elif stripped.startswith("{"):
        found = {f.path for f in peel_json(region).fields if isinstance(f.value, str)}
        field = next((name for name in TEXT_FIELDS if f"json.{name}" in found), None)
        layers.append({"json": {"text_field": field} if field else {}})
    return layers


def peel(raw: bytes, layers: Layers, *, max_depth: int = 4) -> Peeled:
    text = decode(raw).text
    outcome = run_layers(text, layers, max_depth=max_depth)
    region = outcome.text_field
    if region is None:
        return Peeled(raw, text, text, 0)
    return Peeled(raw, text, region.text, region.span[0])
