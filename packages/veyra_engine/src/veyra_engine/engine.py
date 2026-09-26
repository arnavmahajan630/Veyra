"""The S0 **stub** engine: real signatures, tier-4 behaviour.

Why a stub at all: B can index and C can build the console before A3 exists, as long as
the shape of the output never changes (01_TEAM_GUIDE §2). So this file is deliberately
honest — it emits a complete IF-ULPF, a valid IF-NORM-EVENT body and an IF-DLQ record,
and marks everything ``tier 4 / unparseable`` with ``parse_path == ["stub"]``.

What A3 replaces: ``normalize`` internals (decode, peel, template match, map, time,
enrich, validate, tier decision, build) and ``peel``. What A3 must **not** change: the
names, arguments and return types here, nor ``template_sig``/``extract_tokens``.
"""

from __future__ import annotations

import base64
import json
from calendar import timegm
from datetime import datetime
from typing import Any

from charset_normalizer import from_bytes
from veyra_common.hashing import template_sig
from veyra_common.models import DlqRecord, Envelope

from veyra_engine.types import (
    BacktestResult,
    Check,
    EngineContext,
    NormResult,
    PeelResult,
)

ENGINE_VERSION = "0.1.0-s0-stub"
STUB_CATEGORY = "uncategorized"


def decode(raw: bytes) -> tuple[str, str, float, int]:
    """``(text, encoding, confidence, invalid_bytes)`` — UTF-8 strict, then best guess.

    A3 replaces this with the version that also builds the char->byte offset map.
    """
    try:
        return raw.decode("utf-8"), "utf-8", 1.0, 0
    except UnicodeDecodeError:
        best = from_bytes(raw).best()
        if best is not None:
            return str(best), best.encoding, 1.0 - min(best.chaos, 1.0), 0
        text = raw.decode("utf-8", errors="replace")
        return text, "utf-8", 0.0, text.count("�")


class Engine:
    """Deterministic, pure normalizer. No I/O, no clock, no network (P3)."""

    def __init__(self, ctx: EngineContext | None = None) -> None:
        self.ctx = ctx or EngineContext()
        self._active: dict[str, dict[str, Any]] = {}
        self._candidates: dict[str, dict[str, Any]] = {}

    # ---------------------------------------------------------------- contract set
    def load(self, compiled: list[dict[str, Any]]) -> None:
        """Replace the active contract set atomically (copy-on-write swap)."""
        self._active = {c["contract"]: c for c in compiled}

    def set_candidate(self, compiled: dict[str, Any] | None, contract_id: str) -> None:
        """Attach or clear the canary version for one contract (A5 shadow mode)."""
        if compiled is None:
            self._candidates.pop(contract_id, None)
        else:
            self._candidates[contract_id] = compiled

    @property
    def contracts_loaded(self) -> int:
        return len(self._active)

    def contract_for_source(self, source_id: str) -> dict[str, Any] | None:
        """The active contract covering ``source_id``, if any."""
        for contract in self._active.values():
            if source_id in contract.get("sources", []):
                return contract
        return None

    # ---------------------------------------------------------------- pipeline
    def peel(self, envelope: Envelope) -> PeelResult:
        """Stub: no layers are applied, the whole decoded body is the text field."""
        text, _, _, _ = decode(envelope.raw_bytes)
        return PeelResult(
            layers=[],
            fields=[],
            text_field=text,
            text_span=(0, len(text)),
            depth=0,
            error="s0-stub: peeling lands in A3",
        )

    def normalize(self, envelope: Envelope, *, use_candidate: bool = False) -> NormResult:
        """Stub: always tier 4, but with a complete, valid record shape.

        ``use_candidate`` is accepted so A5's shadow path can be wired before the real
        engine exists; the stub ignores it beyond recording it in ``ulpf.shadow``.
        """
        raw = envelope.raw_bytes
        text, encoding, confidence, invalid = decode(raw)
        contract = self.contract_for_source(envelope.source_id)
        scope = contract["contract"] if contract else (envelope.vendor or "unregistered")
        sig = template_sig(scope, text)

        ulpf: dict[str, Any] = {
            "v": 1,
            "event_uid": envelope.event_uid,
            "tenant_id": envelope.tenant_id,
            "source_id": envelope.source_id,
            "vendor": envelope.vendor,
            "zone": envelope.zone,
            # The normalizer service fills raw_ref from the Kafka message coordinates.
            "raw_ref": {"topic": f"raw.{envelope.vendor}", "partition": 0, "offset": 0},
            "raw_sha256": envelope.raw_sha256,
            "received_time": envelope.received_time,
            "custody": envelope.custody,
            "contract": (
                {"id": contract["contract"], "version": contract["version"]} if contract else None
            ),
            "template": {"sig": sig, "id": None},
            "tier": 4,
            "conformance": "unparseable",
            "parse_path": ["stub"],
            # field_offsets stays empty in the stub; A4 fills it and every value there
            # must slice back out of the raw bytes (P4).
            "field_offsets": {},
            "derived_fields": {"time": "received"},
            "class_hint": None,
            "time": {
                "source": "received",
                "tz_assumed": None,
                "year_inferred": False,
                "clock_skew_ms": None,
            },
            "encoding": {
                "detected": encoding,
                "confidence": round(confidence, 2),
                "invalid_bytes": invalid,
            },
            "pii_fields": [],
            "revision": 1,
            "supersedes": None,
            "replay": False,
            "shadow": bool(use_candidate),
            "engine_version": ENGINE_VERSION,
        }

        ocsf: dict[str, Any] = {
            "class_uid": 0,
            "category_uid": 0,
            "type_uid": 99,
            "activity_id": 99,
            "severity_id": 0,
            # Pure: the only clock the engine may read is the one on the envelope.
            "time": _epoch_ms(envelope.received_time),
            "message": text[:1024],
            "raw_data": text,
            "observables": [],
            "unmapped": {},
            "metadata": {
                "version": self.ctx.ocsf_version,
                "product": {"name": "VEYRA", "vendor_name": "NTRO"},
            },
            "ulpf": ulpf,
        }

        dlq = DlqRecord(
            event_uid=envelope.event_uid,
            tenant_id=envelope.tenant_id,
            source_id=envelope.source_id,
            tier=4,
            reason_code="no_contract" if contract is None else "no_template_match",
            reason_detail="s0-stub engine: real parsing lands in A3",
            contract_ref=(f"{contract['contract']}@{contract['version']}" if contract else None),
            template_sig=sig,
            text_masked=text[:2048],
            parse_path=["stub"],
            produced_at=envelope.received_time,
        )

        return NormResult(
            ocsf=ocsf,
            ulpf=ulpf,
            tier=4,
            conformance="unparseable",
            category=STUB_CATEGORY,
            dlq=dlq,
            timings_us={},
            parse_path=["stub"],
        )


def _epoch_ms(received_time: str) -> int:
    """RFC3339-with-nanos (always UTC) -> epoch milliseconds, as an int.

    ``timegm`` keeps this independent of the host timezone, which matters: the same
    envelope must normalize identically on every machine (determinism test, A3 task 8).
    """
    head, _, frac = received_time.rstrip("Z").partition(".")
    seconds = timegm(datetime.strptime(head, "%Y-%m-%dT%H:%M:%S").timetuple())
    nanos = int((frac + "000000000")[:9]) if frac else 0
    return seconds * 1000 + nanos // 1_000_000


def serialize(event: dict[str, Any]) -> bytes:
    """Deterministic serialization: sorted keys, compact separators, no floats for time."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def provenance_check(event: dict[str, Any], raw_bytes: bytes) -> list[Check]:
    """Stub: nothing is mapped yet, so every declared offset trivially holds.

    A4 ships the real check: ``raw_bytes[start:end]`` must equal the value as it appears
    in the raw, and any mapped path without an offset must be in ``derived_fields``.
    """
    ulpf = event.get("ulpf", {})
    checks: list[Check] = []
    for path, span in ulpf.get("field_offsets", {}).items():
        start, end = span
        ok = 0 <= start <= end <= len(raw_bytes)
        checks.append(
            Check(ocsf_path=path, ok=ok, reason="" if ok else "span outside raw", span=(start, end))
        )
    return checks


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Stub: A4 ships the real deterministic masker. IPs are kept on purpose."""
    return text, {}


def backtest(
    active: dict[str, Any] | None,
    candidate: dict[str, Any],
    envelopes: list[Envelope],
) -> BacktestResult:
    """Stub: A5 ships the real backtest. Returns "nothing changed" over the input."""
    engine = Engine()
    if active:
        engine.load([active])
    tiers = {4: len(envelopes)}
    return BacktestResult(
        n=len(envelopes),
        tier_before=dict(tiers),
        tier_after=dict(tiers),
        upgraded=0,
        regressed=0,
        unchanged=len(envelopes),
        examples=[],
        field_coverage={},
    )


def b64_raw(envelope: Envelope) -> bytes:
    """Convenience for tools that only have the JSON form of an envelope."""
    return base64.b64decode(envelope.raw_b64)
