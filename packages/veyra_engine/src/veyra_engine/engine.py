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

All four tiers are live as of A4: ``_tier3`` runs the classifier cascade and generic extraction
(:mod:`veyra_engine.tier3`), so an event nobody wrote a contract for still arrives with its IPs,
users and key/values extracted and byte-located.

Three guards keep the hot path honest under hostile input (A4): a per-event **budget** that aborts
to tier 4 ``budget_exceeded``, a **size cap** that parses only the first ``max_event_bytes`` while
keeping the full raw, and a top-level **exception guard** that turns anything unexpected into tier 4
``engine_crash``. ``normalize`` never raises.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from veyra_common.hashing import template_sig
from veyra_common.ids import monotonic_us
from veyra_common.models import DlqRecord, Envelope
from veyra_engine import tier3
from veyra_engine import validate as validate_module
from veyra_engine.decode import Decoded, decode, decode_text_only
from veyra_engine.mapping import MappingResult, apply_map
from veyra_engine.mask import mask
from veyra_engine.peel import PeeledField, Region, run_layers
from veyra_engine.template import CompiledTemplate, TemplateError, compile_template, match_templates
from veyra_engine.timeparse import parse_time, to_epoch_ms
from veyra_engine.types import (
    BacktestResult,
    Budget,
    Check,
    EngineContext,
    Field,
    NormResult,
    PeelResult,
    Token,
)

ENGINE_VERSION = "0.4.0"


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
        # Set per event when the size cap trims what gets parsed; raw_data still carries it all.
        self._full_text: str | None = None
        # The most recent decode, reused by the budget guard instead of decoding twice.
        self._last_decoded: Decoded | None = None
        self._warm()

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
        self._warm(active.values())

    def _warm(self, prepared: Any = ()) -> None:
        """Pay the one-time costs now, so the first event is not charged for them.

        Without this the budget guard fired on a perfectly good tier-1 event: a cold process spends
        ~14 ms compiling the OCSF validator for a class (fastjsonschema generates Python and
        `exec`s it) and a few more loading a timezone. The budget catches a pathological *input*, so
        anything that happens once per process must happen before the first event, not during it.
        """
        from veyra_engine.validate import fast_validator_for

        classes = {0}
        zones: set[str] = set()
        for contract in prepared:
            time_spec = contract.raw.get("time") or {}
            if time_spec.get("timezone"):
                zones.add(str(time_spec["timezone"]))
            for template in contract.templates:
                classes.add(template.class_uid)
        # Tier 3 always emits Base Event, and every class a loaded contract can produce.
        for class_uid in classes:
            try:
                fast_validator_for(class_uid)
            except Exception:  # an uncompilable schema is reported per event, not here
                continue
        for zone in zones or {"UTC"}:
            # zoneinfo caches per process, but the first lookup reads and parses a file.
            parse_time(
                "2026-01-01 00:00:00",
                received_time="2026-01-01T00:00:00.000000000Z",
                tz=zone,
            )

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
        """Normalize one envelope. Never raises, and never runs away with the CPU (P2, A4).

        Three guards, in the only order that works: the exception guard wraps everything, the budget
        is checked after the work (a pure function cannot be interrupted mid-flight without threads,
        and threads would break determinism), and the size cap is applied inside ``_normalize``
        before any parsing so a 200 KB line never reaches a regex.
        """
        budget = Budget.start(self.ctx.budget_us)
        started = budget.started_us
        try:
            result = self._normalize(envelope, use_candidate=use_candidate, budget=budget)
        except Exception as exc:
            # Any unexpected failure is data, not an outage: tier 4 with the class name, so a
            # recurring crash is diagnosable from the DLQ alone.
            elapsed = monotonic_us() - started
            result = self._tier4(
                envelope,
                decoded=None,
                reason_code="engine_crash",
                detail=f"{type(exc).__name__}: {exc}",
                parse_path=["crash"],
            )
            result.timings_us["total"] = elapsed
            return result

        result.timings_us.setdefault("total", monotonic_us() - started)
        return result

    def _normalize(
        self, envelope: Envelope, *, use_candidate: bool, budget: Budget | None = None
    ) -> NormResult:
        raw = envelope.raw_bytes
        if not raw:
            return self._tier4(envelope, None, "decode_error", "empty payload", ["empty"])

        # Size cap: parse only the first max_event_bytes, but keep the whole event in raw_data so
        # nothing is lost and a verifier still sees what arrived (P1).
        truncated_for_parsing = len(raw) > self.ctx.max_event_bytes
        parse_bytes = raw[: self.ctx.max_event_bytes] if truncated_for_parsing else raw

        decoded = decode(parse_bytes)
        self._last_decoded = decoded
        if truncated_for_parsing:
            # raw_data carries every byte; offsets stay valid because the prefix bytes are the
            # same, and the full text needs no offset map (building that map is the expensive part).
            decoded = Decoded(
                text=decoded.text,
                encoding=decoded.encoding,
                confidence=decoded.confidence,
                invalid_bytes=decoded.invalid_bytes,
                raw=raw,
                char_to_byte=decoded.char_to_byte,
                lossy=decoded.lossy,
            )
            self._full_text = decode_text_only(raw)
        else:
            self._full_text = None
        if decoded.invalid_bytes and decoded.confidence == 0.0 and not decoded.text.strip():
            return self._tier4(envelope, decoded, "decode_error", "no decodable text", ["decode"])

        if budget is not None and budget.expired():
            # Decoding a very large event can use the whole budget on its own.
            return self._over_budget(envelope, decoded, budget, ["decode"], None)

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

        if budget is not None and budget.expired():
            return self._over_budget(envelope, decoded, budget, parse_path or ["peel"], None)

        scope = prepared.id if prepared else (envelope.vendor or "unregistered")
        sig = template_sig(scope, text_region.text)

        if budget is not None and budget.expired():
            return self._over_budget(envelope, decoded, budget, [*parse_path, "sig"], sig)

        # ---- no contract at all: that is the tier-3 path A4 completes
        if prepared is None:
            return self._tier3(
                envelope,
                decoded,
                sig=sig,
                parse_path=parse_path or ["no_contract"],
                reason_code="no_contract",
                detail=f"no contract covers source {envelope.source_id}",
                text=text_region.text,
                peeled=peeled,
                use_candidate=use_candidate,
                budget=budget,
            )

        # ---- template match
        matched = match_templates(prepared.templates, text_region.text, offset=text_region.start)
        if matched is None:
            return self._tier3(
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
                budget=budget,
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
            "raw_data": self._full_text or decoded.text,
            "observables": mapped.observables,
            "unmapped": mapped.unmapped,
            "metadata": {
                "version": self.ctx.ocsf_version,
                "product": {"name": "VEYRA", "vendor_name": "NTRO"},
            },
        }
        # Anything the builder defaulted rather than read from the source has to say so, or
        # provenance_check reports it as an unexplained claim (P4).
        if "severity_id" not in mapped.ocsf:
            mapped.derived_fields.setdefault("severity_id", "default:informational")
        if "message" not in mapped.ocsf and "message" not in mapped.field_offsets:
            mapped.derived_fields.setdefault("message", "default:text_field")

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

    def _over_budget(
        self,
        envelope: Envelope,
        decoded: Decoded | None,
        budget: Budget,
        parse_path: list[str],
        sig: str | None,
    ) -> NormResult:
        """Stop here: the event has used its time. Still delivered, still carries its bytes (P2)."""
        elapsed = budget.elapsed_us()
        result = self._tier4(
            envelope,
            decoded,
            "budget_exceeded",
            f"{elapsed} us over the {budget.limit_us} us budget",
            [*parse_path, "budget_exceeded"],
            sig=sig,
        )
        result.timings_us["total"] = elapsed
        return result

    def _tier4(
        self,
        envelope: Envelope,
        decoded: Decoded | None,
        reason_code: str,
        detail: str,
        parse_path: list[str],
        sig: str | None = None,
    ) -> NormResult:
        """Nothing extractable — but still a complete, valid, deliverable event (P2).

        ``sig`` is passed in when the caller already computed it (the budget guard does): hashing a
        65 KB body a second time is exactly the cost we are there to report.
        """
        text = decoded.text if decoded else ""
        if sig is None:
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
        # Tier 4 sets every value itself; none of them is a claim about the source.
        ulpf["derived_fields"] = {
            **ulpf.get("derived_fields", {}),
            "severity_id": "default:unknown",
            "message": "default:raw_prefix",
        }
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

    def _tier3(
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
        budget: Budget | None = None,
    ) -> NormResult:
        """Generic extraction for anything no template matched (A4).

        The event is still delivered (P2) and still traceable (P4): observables carry byte offsets,
        everything the cascade exposed lands in ``unmapped``, and the honest labels go on —
        ``conformance = "unknown_template"``, ``class_uid`` left at 0, the guess confined to
        ``ulpf.class_hint``. The DLQ copy keeps the real reason so the drift worker (C3) can cluster
        on it.
        """
        extracted = tier3.build(
            decoded,
            envelope.received_time,
            max_depth=self.ctx.peel_max_depth,
            budget=budget,
        )
        if budget is not None and budget.expired():
            return self._over_budget(
                envelope, decoded, budget, [*parse_path, *extracted.parse_path], sig
            )

        # Fields the contract-driven path had already peeled are merged in, so a source that has a
        # contract but no matching template does not lose what the contract's layers exposed.
        for path, peeled_field in peeled.items():
            extracted.unmapped.setdefault(path, peeled_field.value)

        time_result = extracted.time or parse_time(None, received_time=envelope.received_time)
        full_parse_path = parse_path + extracted.parse_path

        ulpf = self._build_ulpf(
            envelope=envelope,
            decoded=decoded,
            tier=3,
            conformance="unknown_template",
            parse_path=full_parse_path,
            sig=sig,
            template_id=None,
            contract=contract,
            mapped=None,
            time_result=time_result,
            use_candidate=use_candidate,
            pii=[],
            class_hint=extracted.class_hint,
        )
        ulpf["field_offsets"] = dict(extracted.field_offsets)
        ulpf["derived_fields"] = dict(extracted.derived_fields)

        event = {
            # Tier 3 stays Base Event: the class hint is a hint, not a mapping.
            "class_uid": 0,
            "category_uid": 0,
            "type_uid": 99,
            "activity_id": 99,
            "severity_id": extracted.severity_id,
            "time": time_result.epoch_ms,
            "message": extracted.text,
            "raw_data": self._full_text or decoded.text,
            "observables": extracted.observables,
            "unmapped": extracted.unmapped,
            "metadata": {
                "version": self.ctx.ocsf_version,
                "product": {"name": "VEYRA", "vendor_name": "NTRO"},
            },
            "ulpf": ulpf,
        }

        return NormResult(
            ocsf=event,
            ulpf=ulpf,
            tier=3,
            conformance="unknown_template",
            category="uncategorized",
            dlq=self._dlq(
                envelope,
                tier=3,
                reason_code=reason_code,
                detail=detail,
                sig=sig,
                parse_path=full_parse_path,
                text=extracted.text,
                contract_ref=(
                    f"{contract.id}@{contract.version}" if contract is not None else None
                ),
            ),
            timings_us={},
            parse_path=full_parse_path,
        )


# ---------------------------------------------------------------- module helpers
def _category_uid(class_uid: int) -> int:
    """OCSF category uid for a class (verified against OCSF 1.9.0)."""
    return {0: 0, 1007: 1, 3002: 3, 4001: 4, 4002: 4}.get(class_uid, 0)


def _dig(value: Any, path: str) -> Any:
    """Resolve a dotted OCSF path, including tier 3's ``observables.<name>`` form.

    ``observables`` is a **list** of ``{name, type_id, value}``, so a plain dict walk cannot reach
    an entry. Tier 3 records its offsets against the observable's name (which is also what the
    console highlights), so that one shape gets an explicit rule rather than silently resolving to
    ``None`` and making every offset look wrong.
    """
    if path.startswith("observables."):
        wanted = path.split(".", 1)[1]
        for observable in value.get("observables", []) if isinstance(value, dict) else []:
            if isinstance(observable, dict) and observable.get("name") == wanted:
                return observable.get("value")
        return None

    cursor = value
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
    return cursor


def serialize(event: dict[str, Any]) -> bytes:
    """Deterministic serialization: sorted keys, compact separators, integer times."""
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _decode_slice(chunk: bytes, encoding: str) -> str:
    """Read a raw slice back with the codec the event says it was decoded in."""
    try:
        return chunk.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return chunk.decode("utf-8", errors="replace")


def provenance_check(event: dict[str, Any], raw_bytes: bytes) -> list[Check]:
    """Verify both halves of P4 for one event.

    1. Every offset in ``ulpf.field_offsets`` must slice out of the raw bytes exactly the value it
       claims — that is what a verifier, the console highlighter (B6) and a judge all rely on.
    2. Every value the event actually carries must be **accounted for**: either located (an offset)
       or declared computed (``ulpf.derived_fields``). A value in neither is an unexplained claim,
       which is the failure this check exists to make impossible to ship.
    """
    ulpf = event.get("ulpf", {})
    # Offsets were built against the codec the engine detected, so a verifier must read the bytes
    # back with that same codec. A Big5 line re-read as UTF-8 slices to replacement characters and
    # would look like a provenance failure when the offsets are in fact exact.
    encoding = str((ulpf.get("encoding") or {}).get("detected") or "utf-8")
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
        actual = _decode_slice(raw_bytes[start:end], encoding)
        ok = expected is not None and str(expected) == actual
        checks.append(
            Check(
                ocsf_path=path,
                ok=ok,
                reason="" if ok else f"raw[{start}:{end}]={actual!r} != {expected!r}",
                span=(start, end),
            )
        )

    # Half two: nothing unexplained.
    located = set(ulpf.get("field_offsets") or {})
    derived = set(ulpf.get("derived_fields") or {})
    for path in _claimed_paths(event):
        if path in located or path in derived:
            continue
        checks.append(
            Check(
                ocsf_path=path,
                ok=False,
                reason="neither located (field_offsets) nor declared computed (derived_fields)",
            )
        )
    return checks


# Fields the engine sets on every event as scaffolding rather than as claims about the source. They
# are the same for every event, so they need no provenance: OCSF classification, our own metadata,
# the raw copy, and the containers for things that carry their own provenance.
_SCAFFOLDING: frozenset[str] = frozenset(
    {
        "class_uid",
        "category_uid",
        "type_uid",
        "activity_id",
        "time",
        "raw_data",
        "metadata",
        "observables",
        "unmapped",
        "enrichments",
        "ulpf",
    }
)


def _claimed_paths(event: dict[str, Any], prefix: str = "") -> list[str]:
    """Every leaf the event asserts about the source, as dotted paths.

    ``observables`` are handled by name (``observables.ip_1``) because tier 3 locates them that way;
    everything under ``unmapped`` is excluded, since it is verbatim parsed data rather than a mapped
    claim, and it is bulky.
    """
    paths: list[str] = []
    for key, value in event.items():
        if not prefix and key in _SCAFFOLDING:
            if key == "observables" and isinstance(value, list):
                for observable in value:
                    if isinstance(observable, dict) and observable.get("name"):
                        paths.append(f"observables.{observable['name']}")
            continue
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            paths.extend(_claimed_paths(value, prefix=f"{path}."))
        elif isinstance(value, list):
            continue
        elif value is not None:
            paths.append(path)
    return paths


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
