"""The deterministic normalizer (IF-ENGINE-LIB). Pure: no I/O, no clock, no network (P3).

Pipeline, in order:

    decode → resolve contract → peel → select text field → match template → map
           → time → enrich → validate → tier decision → build OCSF + ulpf → DLQ record

Tier decision (IF-ULPF), which is the part everything else hangs off:

| condition                                                        | tier | conformance        |
|------------------------------------------------------------------|------|--------------------|
| template matched, required present, schema valid                 | 1    | match              |
| peel or template partially worked, but required/schema failed    | 2    | partial            |
| no contract, or no template matched (generic extraction)          | 3    | unknown_template   |
| decode failure, empty, guard tripped, exception                   | 4    | unparseable        |

A3 builds tiers 1, 2 and 4 and leaves a **deliberate hole at tier 3**: the generic extractor is
A4's job, so an event that would be tier 3 is emitted as tier 4 today, with the code path already
in place (``_tier3_placeholder``). That is the phase file's instruction, not an oversight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from veyra_common.hashing import template_sig
from veyra_common.models import DlqRecord, Envelope
from veyra_engine import validate as validate_module
from veyra_engine.decode import Decoded, decode
from veyra_engine.mapping import MappingResult, apply_map
from veyra_engine.mask import mask
from veyra_engine.peel import PeeledField, Region, run_layers
from veyra_engine.template import CompiledTemplate, TemplateError, compile_template, match_templates
from veyra_engine.timeparse import parse_time, to_epoch_ms
from veyra_engine.types import (
    BacktestResult,
    Check,
    EngineContext,
    Field,
    NormResult,
    PeelResult,
    Token,
)

ENGINE_VERSION = "0.3.0"


@dataclass(slots=True)
class _Prepared:
    """A compiled contract with its templates already turned into RE2 objects."""

    raw: dict[str, Any]
    templates: list[CompiledTemplate]
    errors: list[str]

    @property
    def id(self) -> str:
        return str(self.raw.get("contract", ""))

    @property
    def version(self) -> int:
        return int(self.raw.get("version", 1))

    @property
    def sources(self) -> list[str]:
        return list(self.raw.get("sources") or [])


def _prepare(compiled: dict[str, Any]) -> _Prepared:
    """Compile the templates once, at load time, not per event."""
    templates: list[CompiledTemplate] = []
    errors: list[str] = []
    for spec in compiled.get("templates") or []:
        try:
            templates.append(compile_template(spec))
        except TemplateError as exc:
            # A broken template must not take the whole contract down: the others still work
            # and the failure is visible in the load report.
            errors.append(str(exc))
    return _Prepared(raw=compiled, templates=templates, errors=errors)


class Engine:
    """Normalizes envelopes. Construct once, call :meth:`normalize` per event."""

    def __init__(self, ctx: EngineContext | None = None) -> None:
        self.ctx = ctx or EngineContext()
        self._active: dict[str, _Prepared] = {}
        self._candidates: dict[str, _Prepared] = {}
        self._by_source: dict[str, _Prepared] = {}
        self.load_errors: list[str] = []

    # ---------------------------------------------------------------- contract set
    def load(self, compiled: list[dict[str, Any]]) -> None:
        """Replace the active set atomically (copy-on-write: build, then swap)."""
        active: dict[str, _Prepared] = {}
        by_source: dict[str, _Prepared] = {}
        errors: list[str] = []
        for document in compiled:
            prepared = _prepare(document)
            active[prepared.id] = prepared
            errors.extend(prepared.errors)
            for source_id in prepared.sources:
                by_source[source_id] = prepared
        self._active = active
        self._by_source = by_source
        self.load_errors = errors

    def set_candidate(self, compiled: dict[str, Any] | None, contract_id: str) -> None:
        """Attach or clear a canary version (A5 runs it in shadow)."""
        if compiled is None:
            self._candidates.pop(contract_id, None)
        else:
            self._candidates[contract_id] = _prepare(compiled)

    @property
    def contracts_loaded(self) -> int:
        return len(self._active)

    def contract_for_source(self, source_id: str) -> dict[str, Any] | None:
        prepared = self._by_source.get(source_id)
        return None if prepared is None else prepared.raw

    def _resolve_contract(self, source_id: str, *, use_candidate: bool) -> _Prepared | None:
        """Which contract applies to this source — active, or its candidate in shadow mode.

        Candidates are keyed by **contract id** (that is what `set_candidate` takes), so the
        source's active contract is what points at them. A candidate that declares the source
        itself also counts, which is how a brand-new contract can be shadowed before it is
        active anywhere.
        """
        active = self._by_source.get(source_id)
        if not use_candidate:
            return active
        if active is not None and active.id in self._candidates:
            return self._candidates[active.id]
        for candidate in self._candidates.values():
            if source_id in candidate.sources:
                return candidate
        return active

    # ---------------------------------------------------------------- peel
    def peel(self, envelope: Envelope) -> PeelResult:
        """Run the contract's envelope layers (or none) and report what was exposed."""
        decoded = decode(envelope.raw_bytes)
        prepared = self._by_source.get(envelope.source_id)
        specs = list(prepared.raw.get("envelope") or []) if prepared else []
        outcome = run_layers(decoded.text, specs, max_depth=self.ctx.peel_max_depth)
        return PeelResult(
            layers=list(outcome.layers),
            fields=[
                Field(path=f.path, value=f.value, char_span=f.char_span, layer=f.layer)
                for f in outcome.fields
            ],
            text_field=outcome.text_field.text if outcome.text_field else None,
            text_span=outcome.text_field.span if outcome.text_field else None,
            depth=outcome.depth,
            error=outcome.error,
        )

    # ---------------------------------------------------------------- normalize
    def normalize(self, envelope: Envelope, *, use_candidate: bool = False) -> NormResult:
        """Normalize one envelope. Never raises: any failure becomes tier 4 (P2)."""
        try:
            return self._normalize(envelope, use_candidate=use_candidate)
        except Exception as exc:  # the top-level guard A4 hardens further
            return self._tier4(
                envelope,
                decoded=None,
                reason_code="engine_crash",
                detail=f"{type(exc).__name__}: {exc}",
                parse_path=["crash"],
            )

    def _normalize(self, envelope: Envelope, *, use_candidate: bool) -> NormResult:
        raw = envelope.raw_bytes
        if not raw:
            return self._tier4(envelope, None, "decode_error", "empty payload", ["empty"])

        decoded = decode(raw)
        if decoded.invalid_bytes and decoded.confidence == 0.0 and not decoded.text.strip():
            return self._tier4(envelope, decoded, "decode_error", "no decodable text", ["decode"])

        prepared = self._resolve_contract(envelope.source_id, use_candidate=use_candidate)

        parse_path: list[str] = []

        # ---- peel
        specs = list(prepared.raw.get("envelope") or []) if prepared else []
        outcome = run_layers(decoded.text, specs, max_depth=self.ctx.peel_max_depth)
        parse_path.extend(
            f"{layer}:{'rfc3164' if layer == 'syslog' else 'ok'}" if layer == "syslog" else layer
            for layer in outcome.layers
        )
        peeled: dict[str, PeeledField] = outcome.field_map()
        text_region: Region = outcome.text_field or Region(decoded.text, (0, len(decoded.text)))

        scope = prepared.id if prepared else (envelope.vendor or "unregistered")
        sig = template_sig(scope, text_region.text)

        # ---- no contract at all: that is the tier-3 path A4 completes
        if prepared is None:
            return self._tier3_placeholder(
                envelope,
                decoded,
                sig=sig,
                parse_path=parse_path or ["no_contract"],
                reason_code="no_contract",
                detail=f"no contract covers source {envelope.source_id}",
                text=text_region.text,
                peeled=peeled,
                use_candidate=use_candidate,
            )

        # ---- template match
        matched = match_templates(prepared.templates, text_region.text, offset=text_region.start)
        if matched is None:
            return self._tier3_placeholder(
                envelope,
                decoded,
                sig=sig,
                parse_path=[*parse_path, "no_template_match"],
                reason_code="no_template_match",
                detail=(
                    f"{prepared.id}@{prepared.version} has {len(prepared.templates)} template(s), "
                    "none matched"
                ),
                text=text_region.text,
                peeled=peeled,
                contract=prepared,
                use_candidate=use_candidate,
            )
        parse_path.append(f"template:{matched.template.id}")

        # ---- map
        time_spec = prepared.raw.get("time") or {}

        def resolve_time(value: str, formats: list[str] | None) -> tuple[int | None, str]:
            result = parse_time(
                value,
                received_time=envelope.received_time,
                formats=formats or time_spec.get("formats"),
                tz=time_spec.get("timezone"),
                year_policy=str(time_spec.get("year", "infer_from_received")),
            )
            detail = "inferred_year" if result.year_inferred else (result.format_used or "auto")
            return result.epoch_ms, detail

        mapped = apply_map(
            matched.template.map or [],
            captures=matched.values,
            capture_spans=matched.spans,
            peeled=peeled,
            text=text_region.text,
            text_span=text_region.span,
            decoded=decoded,
            vocab=self.ctx.vocab,
            time_resolver=resolve_time,
        )

        # ---- time (the event's own `time` field, from the contract's time: block)
        time_field_value: Any = None
        time_ref = str(time_spec.get("field", "")).lstrip("$")
        if time_ref:
            if time_ref in matched.values:
                time_field_value = matched.values[time_ref]
            elif time_ref in peeled:
                time_field_value = peeled[time_ref].value
        time_result = parse_time(
            None if time_field_value is None else str(time_field_value),
            received_time=envelope.received_time,
            formats=time_spec.get("formats"),
            tz=time_spec.get("timezone"),
            year_policy=str(time_spec.get("year", "infer_from_received")),
        )

        # ---- enrich
        enrichments = self._enrich(prepared, mapped)

        # ---- build the event
        event = self._build_event(
            envelope=envelope,
            decoded=decoded,
            mapped=mapped,
            template=matched.template,
            time_result=time_result,
            enrichments=enrichments,
            text=text_region.text,
        )

        # ---- validate → tier
        required = list(prepared.raw.get("required") or [])
        validation = validate_module.validate_event(event, required)
        degraded = bool(
            mapped.missing or mapped.coercion_errors or not validation.ok or outcome.error
        )
        tier = 2 if degraded else 1
        conformance = "partial" if degraded else "match"

        ulpf = self._build_ulpf(
            envelope=envelope,
            decoded=decoded,
            tier=tier,
            conformance=conformance,
            parse_path=parse_path,
            sig=sig,
            template_id=matched.template.id,
            contract=prepared,
            mapped=mapped,
            time_result=time_result,
            use_candidate=use_candidate,
            pii=list(prepared.raw.get("pii") or []),
        )
        event["ulpf"] = ulpf

        dlq = None
        if tier >= 2:
            reason_code = (
                "required_missing"
                if (mapped.missing or validation.missing_required)
                else "schema_invalid"
            )
            detail_parts = []
            if mapped.missing:
                detail_parts.append("unmapped paths: " + ", ".join(sorted(set(mapped.missing))))
            if validation.reason:
                detail_parts.append(validation.reason)
            if mapped.coercion_errors:
                detail_parts.append("; ".join(mapped.coercion_errors[:3]))
            if outcome.error:
                detail_parts.append(f"peel: {outcome.error}")
            dlq = self._dlq(
                envelope,
                tier=tier,
                reason_code=reason_code,
                detail=" | ".join(detail_parts)[:2000],
                sig=sig,
                parse_path=parse_path,
                text=text_region.text,
                contract_ref=f"{prepared.id}@{prepared.version}",
            )

        return NormResult(
            ocsf=event,
            ulpf=ulpf,
            tier=tier,
            conformance=conformance,
            category=matched.template.category,
            dlq=dlq,
            timings_us={},
            parse_path=parse_path,
        )

    # ---------------------------------------------------------------- builders
    def _enrich(self, prepared: _Prepared, mapped: MappingResult) -> list[dict[str, Any]]:
        """Offline table lookups, appended as OCSF ``enrichments[]``.

        Enrichment never overwrites a mapped field: it is clearly marked as added by us, so a
        judge (and a verifier) can tell source data from our additions (P4).
        """
        out: list[dict[str, Any]] = []
        for table_name in prepared.raw.get("enrich") or []:
            rows = self.ctx.enrich.get(table_name) or []
            if not rows:
                continue
            for path in ("src_endpoint.ip", "dst_endpoint.ip", "device.hostname"):
                value = _dig(mapped.ocsf, path)
                if not value:
                    continue
                for row in rows:
                    if str(row.get("match_value", row.get("cidr", row.get("value", "")))) == str(
                        value
                    ):
                        out.append(
                            {
                                "name": table_name,
                                "value": str(value),
                                "data": row,
                                "provider": "veyra_enrich",
                            }
                        )
                        break
        return out

    def _build_event(
        self,
        *,
        envelope: Envelope,
        decoded: Decoded,
        mapped: MappingResult,
        template: CompiledTemplate,
        time_result: Any,
        enrichments: list[dict[str, Any]],
        text: str,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "class_uid": template.class_uid,
            "category_uid": _category_uid(template.class_uid),
            "type_uid": template.type_uid or template.class_uid * 100 + template.activity_id,
            "activity_id": template.activity_id,
            "severity_id": 1,
            "time": time_result.epoch_ms,
            "message": text,
            "raw_data": decoded.text,
            "observables": mapped.observables,
            "unmapped": mapped.unmapped,
            "metadata": {
                "version": self.ctx.ocsf_version,
                "product": {"name": "VEYRA", "vendor_name": "NTRO"},
            },
        }
        # Mapped values win over the defaults above (a contract may set severity or message).
        for key, value in mapped.ocsf.items():
            if isinstance(value, dict) and isinstance(event.get(key), dict):
                event[key].update(value)
            else:
                event[key] = value
        if enrichments:
            event["enrichments"] = enrichments
        return event

    def _build_ulpf(
        self,
        *,
        envelope: Envelope,
        decoded: Decoded,
        tier: int,
        conformance: str,
        parse_path: list[str],
        sig: str,
        template_id: str | None,
        contract: _Prepared | None,
        mapped: MappingResult | None,
        time_result: Any,
        use_candidate: bool,
        pii: list[str],
        class_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "v": 1,
            "event_uid": envelope.event_uid,
            "tenant_id": envelope.tenant_id,
            "source_id": envelope.source_id,
            "vendor": envelope.vendor,
            "zone": envelope.zone,
            # The normalizer service overwrites this with the real Kafka coordinates.
            "raw_ref": {"topic": f"raw.{envelope.vendor}", "partition": 0, "offset": 0},
            "raw_sha256": envelope.raw_sha256,
            "received_time": envelope.received_time,
            "custody": envelope.custody,
            "contract": (
                {"id": contract.id, "version": contract.version} if contract is not None else None
            ),
            "template": {"sig": sig, "id": template_id},
            "tier": tier,
            "conformance": conformance,
            "parse_path": parse_path,
            "field_offsets": dict(mapped.field_offsets) if mapped else {},
            "derived_fields": dict(mapped.derived_fields) if mapped else {},
            "class_hint": class_hint,
            "time": {
                "source": time_result.source,
                "tz_assumed": time_result.tz_assumed,
                "year_inferred": time_result.year_inferred,
                "clock_skew_ms": time_result.clock_skew_ms,
            },
            "encoding": {
                "detected": decoded.encoding if decoded else "unknown",
                "confidence": round(decoded.confidence, 2) if decoded else 0.0,
                "invalid_bytes": decoded.invalid_bytes if decoded else 0,
            },
            "pii_fields": [p for p in pii if p in (mapped.field_offsets if mapped else {})],
            "revision": 1,
            "supersedes": None,
            "replay": False,
            "shadow": bool(use_candidate),
            "engine_version": ENGINE_VERSION,
        }

    def _dlq(
        self,
        envelope: Envelope,
        *,
        tier: int,
        reason_code: str,
        detail: str,
        sig: str,
        parse_path: list[str],
        text: str,
        contract_ref: str | None = None,
    ) -> DlqRecord:
        masked, _ = mask(text)
        return DlqRecord(
            event_uid=envelope.event_uid,
            tenant_id=envelope.tenant_id,
            source_id=envelope.source_id,
            tier=tier,
            reason_code=reason_code,  # type: ignore[arg-type]
            reason_detail=detail[:2000],
            contract_ref=contract_ref,
            template_sig=sig,
            text_masked=masked[:2048],
            parse_path=parse_path,
            produced_at=envelope.received_time,
        )

    def _tier4(
        self,
        envelope: Envelope,
        decoded: Decoded | None,
        reason_code: str,
        detail: str,
        parse_path: list[str],
    ) -> NormResult:
        """Nothing extractable — but still a complete, valid, deliverable event (P2)."""
        text = decoded.text if decoded else ""
        sig = template_sig(envelope.vendor or "unregistered", text)
        time_result = parse_time(None, received_time=envelope.received_time)
        ulpf = self._build_ulpf(
            envelope=envelope,
            decoded=decoded or decode(b""),
            tier=4,
            conformance="unparseable",
            parse_path=parse_path,
            sig=sig,
            template_id=None,
            contract=None,
            mapped=None,
            time_result=time_result,
            use_candidate=False,
            pii=[],
        )
        event = {
            "class_uid": 0,
            "category_uid": 0,
            "type_uid": 99,
            "activity_id": 99,
            "severity_id": 0,
            "time": time_result.epoch_ms,
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
        return NormResult(
            ocsf=event,
            ulpf=ulpf,
            tier=4,
            conformance="unparseable",
            category="uncategorized",
            dlq=self._dlq(
                envelope,
                tier=4,
                reason_code=reason_code,
                detail=detail,
                sig=sig,
                parse_path=parse_path,
                text=text,
            ),
            timings_us={},
            parse_path=parse_path,
        )

    def _tier3_placeholder(
        self,
        envelope: Envelope,
        decoded: Decoded,
        *,
        sig: str,
        parse_path: list[str],
        reason_code: str,
        detail: str,
        text: str,
        peeled: dict[str, PeeledField],
        contract: _Prepared | None = None,
        use_candidate: bool = False,
    ) -> NormResult:
        """The tier-3 slot. A4 fills it; A3 emits tier 4 here, as the phase file says.

        The peeled fields are still carried in ``unmapped`` so nothing is lost in the meantime,
        and the DLQ reason is the real one (``no_contract`` / ``no_template_match``) rather than
        a pretend crash — which is what the drift worker (C3) will cluster on.
        """
        time_result = parse_time(None, received_time=envelope.received_time)
        ulpf = self._build_ulpf(
            envelope=envelope,
            decoded=decoded,
            tier=4,
            conformance="unparseable",
            parse_path=parse_path,
            sig=sig,
            template_id=None,
            contract=contract,
            mapped=None,
            time_result=time_result,
            use_candidate=use_candidate,
            pii=[],
        )
        event = {
            "class_uid": 0,
            "category_uid": 0,
            "type_uid": 99,
            "activity_id": 99,
            "severity_id": 0,
            "time": time_result.epoch_ms,
            "message": text[:1024],
            "raw_data": decoded.text,
            "observables": [],
            "unmapped": {path: f.value for path, f in peeled.items()},
            "metadata": {
                "version": self.ctx.ocsf_version,
                "product": {"name": "VEYRA", "vendor_name": "NTRO"},
            },
            "ulpf": ulpf,
        }
        return NormResult(
            ocsf=event,
            ulpf=ulpf,
            tier=4,
            conformance="unparseable",
            category="uncategorized",
            dlq=self._dlq(
                envelope,
                tier=4,
                reason_code=reason_code,
                detail=detail,
                sig=sig,
                parse_path=parse_path,
                text=text,
                contract_ref=(
                    f"{contract.id}@{contract.version}" if contract is not None else None
                ),
            ),
            timings_us={},
            parse_path=parse_path,
        )


# ---------------------------------------------------------------- module helpers
def _category_uid(class_uid: int) -> int:
    """OCSF category uid for a class (verified against OCSF 1.9.0)."""
    return {0: 0, 1007: 1, 3002: 3, 4001: 4, 4002: 4}.get(class_uid, 0)


def _dig(value: Any, path: str) -> Any:
    cursor = value
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def serialize(event: dict[str, Any]) -> bytes:
    """Deterministic serialization: sorted keys, compact separators, integer times."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def provenance_check(event: dict[str, Any], raw_bytes: bytes) -> list[Check]:
    """Does every declared offset really slice the value it claims out of the raw bytes?

    A4 extends this with the "every mapped path is either located or derived" half; the byte
    check is here because A3's mapper is what produces the offsets.
    """
    ulpf = event.get("ulpf", {})
    checks: list[Check] = []
    for path, span in (ulpf.get("field_offsets") or {}).items():
        try:
            start, end = int(span[0]), int(span[1])
        except (TypeError, ValueError, IndexError):
            checks.append(Check(ocsf_path=path, ok=False, reason="malformed span"))
            continue
        if not (0 <= start <= end <= len(raw_bytes)):
            checks.append(
                Check(ocsf_path=path, ok=False, reason="span outside raw", span=(start, end))
            )
            continue
        expected = _dig(event, path)
        actual = raw_bytes[start:end].decode("utf-8", errors="replace")
        ok = expected is not None and str(expected) == actual
        checks.append(
            Check(
                ocsf_path=path,
                ok=ok,
                reason="" if ok else f"raw[{start}:{end}]={actual!r} != {expected!r}",
                span=(start, end),
            )
        )
    return checks


def backtest(
    active: dict[str, Any] | None,
    candidate: dict[str, Any],
    envelopes: list[Envelope],
) -> BacktestResult:
    """What would the candidate contract do to these stored events? (A5 refines this.)"""
    before = Engine()
    if active:
        before.load([active])
    after = Engine()
    after.load([candidate])

    tier_before: dict[int, int] = {}
    tier_after: dict[int, int] = {}
    upgraded = regressed = unchanged = 0
    examples: list[dict[str, Any]] = []

    for envelope in envelopes:
        old = before.normalize(envelope)
        new = after.normalize(envelope)
        tier_before[old.tier] = tier_before.get(old.tier, 0) + 1
        tier_after[new.tier] = tier_after.get(new.tier, 0) + 1
        if new.tier < old.tier:
            upgraded += 1
        elif new.tier > old.tier:
            regressed += 1
        else:
            unchanged += 1
        if len(examples) < 20:
            examples.append(
                {
                    "event_uid": envelope.event_uid,
                    "before_tier": old.tier,
                    "after_tier": new.tier,
                    "changed_fields": sorted(
                        set(new.ulpf.get("field_offsets", {}))
                        ^ set(old.ulpf.get("field_offsets", {}))
                    ),
                    "provenance_ok": all(
                        check.ok for check in provenance_check(new.ocsf, envelope.raw_bytes)
                    ),
                }
            )

    return BacktestResult(
        n=len(envelopes),
        tier_before=tier_before,
        tier_after=tier_after,
        upgraded=upgraded,
        regressed=regressed,
        unchanged=unchanged,
        examples=examples,
        field_coverage={},
    )


def extract_tokens(text: str) -> list[Token]:
    """Re-exported from :mod:`veyra_engine.tokens` (A4 finalizes the catalogue)."""
    from veyra_engine.tokens import extract_tokens as _extract

    return _extract(text)


__all__ = [
    "ENGINE_VERSION",
    "Engine",
    "backtest",
    "decode",
    "extract_tokens",
    "provenance_check",
    "serialize",
    "to_epoch_ms",
]
