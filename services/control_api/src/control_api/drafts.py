"""Drafts (C4): a drift item or pasted samples → a reviewed, verified contract version.

One ``drafts`` row per draft. ``payload_json`` holds everything the review page needs; the
row's own columns are what lists and scoping need. Drift drafting runs on ``ctx.spawn``
and takes one of ``ctx.draft_slots`` while the model is working (TC38).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session as DbSession

from control_api.backtest import run_backtest
from control_api.context import AppContext
from control_api.drift import drift_event
from control_api.evidence import EvidenceUnavailable
from control_api.registry import compiled_of, engine_context
from control_api.tables import Contract, ContractVersion, Draft, DriftItem, Source
from veyra_common.ids import uuid7
from veyra_contracts import compile
from veyra_contracts.drafting.drafter import Outcome
from veyra_contracts.drafting.generalize import (
    TemplateDraft,
    add_template,
    existing_template_ids,
    generalize,
    new_contract,
)
from veyra_contracts.drafting.request import Prepared, build, sample_group
from veyra_contracts.drafting.schema import DraftResponse, problems
from veyra_contracts.drafting.verify import verify


class DraftOut(BaseModel):
    draft_id: str
    tenant_id: str
    source_id: str | None
    drift_id: str | None
    contract_id: str | None
    state: str
    detail: str
    templates: list[dict[str, Any]]
    yaml: str
    verification: dict[str, Any] | None
    backtest: dict[str, Any] | None
    library: list[dict[str, Any]]
    created_by: str | None
    created_at: str
    updated_at: str | None


class MappingEdit(BaseModel):
    ocsf_path: str
    token: str | None = None
    const: int | None = None


class DraftEdit(BaseModel):
    template_sig: str | None = None
    class_: str | None = Field(default=None, alias="class")
    activity: str | None = None
    mappings: list[MappingEdit] | None = None


class EditRejected(ValueError):
    """The edit points outside the closed vocabulary."""


def contract_id_for(source_id: str) -> str:
    """``src_authsrv_01`` → ``authsrv`` (TC32)."""
    return re.sub(r"_\d+$", "", source_id.removeprefix("src_")) or "contract"


def payload(draft: Draft) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(draft.payload_json)
    return data


def draft_out(draft: Draft) -> DraftOut:
    data = payload(draft)
    return DraftOut(
        draft_id=draft.draft_id,
        tenant_id=draft.tenant_id,
        source_id=draft.source_id,
        drift_id=draft.drift_id,
        contract_id=draft.contract_id,
        state=draft.state,
        detail=draft.detail,
        templates=data.get("templates", []),
        yaml=data.get("yaml", ""),
        verification=data.get("verification"),
        backtest=data.get("backtest"),
        library=data.get("library", []),
        created_by=draft.created_by,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def draft_event(draft: Draft) -> dict[str, Any]:
    return {
        "draft_id": draft.draft_id,
        "drift_id": draft.drift_id,
        "state": draft.state,
        "source_id": draft.source_id,
    }


def _template_from_entry(entry: dict[str, Any]) -> TemplateDraft:
    raw = entry["template"]
    return TemplateDraft(
        id=raw["id"],
        pattern=raw["pattern"],
        class_=raw["class"],
        activity=raw["activity"],
        map=raw["map"],
        unmapped=list(raw.get("unmapped", [])),
    )


def template_entry(prepared: Prepared, outcome: Outcome, template: TemplateDraft) -> dict[str, Any]:
    sample = prepared.samples[0]
    return {
        "template_sig": prepared.template_sig,
        "drain_template": prepared.drain_template,
        "request": prepared.request,
        "response": outcome.response.dump(),
        "source": outcome.source,
        "latency_ms": outcome.latency_ms,
        "review": outcome.review,
        "notes": outcome.notes,
        "template": template.as_yaml_dict(),
        "sample_text": sample.text,
        "spans": [
            {
                "id": t.id,
                "value": t.value,
                "kind": t.kind,
                "start": sample.template_start + t.start,
                "end": sample.template_start + t.end,
            }
            for t in prepared.tokens
            if t.id in prepared.variable
        ],
    }


def _active(db: DbSession, contract_id: str) -> ContractVersion | None:
    contract = db.get(Contract, contract_id)
    if contract is None or contract.active_version is None:
        return None
    return db.get(ContractVersion, (contract_id, contract.active_version))


def _raw_samples(ctx: AppContext, sig: str) -> list[bytes]:
    if ctx.index is None or ctx.raw is None:
        return []
    try:
        refs = ctx.index.template_events(sig, limit=ctx.cfg.drift_max_samples)
        return [env.raw_bytes for env in ctx.raw.envelopes(refs)]
    except EvidenceUnavailable:
        return []


def _assemble(
    ctx: AppContext,
    db: DbSession,
    draft: Draft,
    prepared: Prepared,
    outcome: Outcome,
    *,
    drafted_by: str,
) -> dict[str, Any]:
    """Pattern, YAML, verification and backtest for a one-template draft."""
    active = _active(db, draft.contract_id or "")
    taken = existing_template_ids(active.yaml) if active is not None else set()
    template = generalize(prepared, outcome.response, taken_ids=taken)
    if active is not None:
        yaml_text = add_template(
            active.yaml, template, drafted_by=drafted_by, draft_id=draft.draft_id
        )
    else:
        yaml_text = new_contract(
            contract_id=draft.contract_id or "contract",
            tenant_id=draft.tenant_id,
            source_id=draft.source_id or "",
            layers=prepared.layers,
            templates=[template],
            timezone=ctx.cfg.drafter_timezone,
            drafted_by=drafted_by,
            draft_id=draft.draft_id,
        )
    samples = [s.raw for s in prepared.samples]
    verification = verify(yaml_text, samples, {template.id}, ctx=engine_context(ctx))
    backtest = None
    if verification.compile_error is None:
        backtest = run_backtest(
            compiled_of(active) if active is not None else None,
            compile(yaml_text).to_dict(),
            index=ctx.index,
            raw=ctx.raw,
            sigs=[prepared.template_sig],
            limit=ctx.cfg.backtest_max,
        )
    return {
        "templates": [template_entry(prepared, outcome, template)],
        "yaml": yaml_text,
        "verification": verification.to_dict(),
        "backtest": backtest,
    }


def start_drift_draft(
    ctx: AppContext, db: DbSession, item: DriftItem, *, actor: str, mode: str | None = None
) -> Draft:
    source = db.get(Source, item.source_id)
    contract_id = (
        source.contract_id if source and source.contract_id else contract_id_for(item.source_id)
    )
    draft = Draft(
        draft_id=f"dr_{uuid7().hex}",
        tenant_id=item.tenant_id,
        source_id=item.source_id,
        drift_id=item.drift_id,
        contract_id=contract_id,
        state="drafting",
        created_by=actor,
        created_at=ctx.now(),
        updated_at=ctx.now(),
    )
    item.state, item.draft_id, item.updated_at = "drafting", draft.draft_id, ctx.now()
    db.add(draft)
    db.add(item)
    db.commit()
    db.refresh(draft)
    ctx.hub.publish("draft", draft_event(draft))
    ctx.spawn(lambda: run_drift_draft(ctx, draft.draft_id, mode))
    return draft


def run_drift_draft(ctx: AppContext, draft_id: str, mode: str | None) -> None:
    with DbSession(ctx.engine) as db:
        draft = db.get(Draft, draft_id)
        item = db.get(DriftItem, draft.drift_id or "") if draft else None
        if draft is None or item is None or ctx.drafter is None:
            return
        raws = _raw_samples(ctx, item.template_sig)
        detail = ""
        active = _active(db, draft.contract_id or "")
        layers = compile(active.yaml).envelope if active is not None else None
        if not raws:
            raws = [t.encode("utf-8") for t in json.loads(item.samples_masked_json)]
            layers = []
            detail = "no raw events available: drafted and verified on masked template text"
        try:
            prepared = build(
                raws,
                template_sig=item.template_sig,
                drain_template=item.drain_template,
                layers=layers,
            )
            with ctx.draft_slots:
                outcome = ctx.drafter.draft(prepared, mode=mode)
            data = _assemble(ctx, db, draft, prepared, outcome, drafted_by=outcome.source)
        except Exception as exc:
            draft.state, draft.detail = "failed", f"{type(exc).__name__}: {exc}"
            item.state = "open"
        else:
            data |= {
                "kind": "drift",
                "layers": prepared.layers,
                "raw_available": not detail,
                "samples_b64": [base64.b64encode(r).decode() for r in raws],
            }
            draft.payload_json = json.dumps(data)
            draft.state, draft.detail = "ready", detail
            item.state = "draft_ready"
        draft.updated_at = item.updated_at = ctx.now()
        db.add(draft)
        db.add(item)
        db.commit()
        db.refresh(draft)
        db.refresh(item)
    ctx.hub.publish("draft", draft_event(draft))
    ctx.hub.publish("drift", drift_event(item))


def apply_edit(ctx: AppContext, db: DbSession, draft: Draft, edit: DraftEdit) -> Draft:
    """Rewrite one template's mapping (by hand), then regenerate, re-verify, re-backtest."""
    data = payload(draft)
    templates = data["templates"]
    index = next((i for i, t in enumerate(templates) if t["template_sig"] == edit.template_sig), 0)
    entry = templates[index]
    response = DraftResponse.model_validate(entry["response"])
    changed = response.dump()
    if edit.class_ is not None:
        changed["class"] = edit.class_
    if edit.activity is not None:
        changed["activity"] = edit.activity
    if edit.mappings is not None:
        changed["mappings"] = [m.model_dump(exclude_none=True) for m in edit.mappings]
    new_response = DraftResponse.model_validate(changed)
    found = problems(new_response, {t["id"] for t in entry["request"]["tokens"]})
    if found:
        raise EditRejected("; ".join(found))
    raws = [base64.b64decode(s) for s in data.get("samples_b64", [])]
    if data.get("kind") == "onboarding":
        from veyra_common.hashing import template_sig as sig_of
        from veyra_contracts.drafting.classify import peel as peel_sample

        def skeleton(raw: bytes) -> tuple[tuple[str, str | None], ...]:
            return sample_group(peel_sample(raw, data["layers"]).template_text)

        wanted = next(
            (
                skeleton(raw)
                for raw in raws
                if sig_of(draft.contract_id or "", peel_sample(raw, data["layers"]).template_text)
                == entry["template_sig"]
            ),
            None,
        )
        if wanted is not None:
            raws = [raw for raw in raws if skeleton(raw) == wanted]
    prepared = build(
        raws,
        template_sig=entry["template_sig"],
        drain_template=entry["drain_template"],
        layers=data["layers"],
    )
    outcome = Outcome(new_response, "human", 0.0)
    rebuilt = _assemble(ctx, db, draft, prepared, outcome, drafted_by="human")
    templates[index] = rebuilt["templates"][0] | {"review": entry.get("review", [])}
    data |= {k: rebuilt[k] for k in ("yaml", "verification", "backtest")}
    data["templates"] = templates
    if data.get("kind") == "onboarding":
        drafted = [_template_from_entry(item) for item in templates]
        yaml_text = new_contract(
            contract_id=draft.contract_id or "contract",
            tenant_id=draft.tenant_id,
            source_id=draft.source_id or "",
            layers=data["layers"],
            templates=drafted,
            timezone=ctx.cfg.drafter_timezone,
            drafted_by="human",
            draft_id=draft.draft_id,
        )
        all_raws = [base64.b64decode(s) for s in data.get("samples_b64", [])]
        data["yaml"] = yaml_text
        data["verification"] = verify(
            yaml_text, all_raws, {item.id for item in drafted}, ctx=engine_context(ctx)
        ).to_dict()
        data["backtest"] = None
    draft.payload_json = json.dumps(data)
    draft.updated_at = ctx.now()
    db.add(draft)
    return draft
