"""Onboarding analyze (C4): pasted samples → layers → groups → library match or drafts.

Streamed as SSE frames so the console fills in step by step (C6 Beat 2). The groups are
by ``template_sig`` with the future contract id as scope (TC32), so the sigs are the ones
the normalizer will compute once the contract is active.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session as DbSession

from control_api.auth import Principal
from control_api.context import AppContext
from control_api.drafts import contract_id_for, template_entry
from control_api.registry import engine_context
from control_api.tables import Draft, Source
from veyra_common.hashing import template_sig
from veyra_common.ids import uuid7
from veyra_contracts.drafting.classify import classify, peel
from veyra_contracts.drafting.generalize import generalize, new_contract
from veyra_contracts.drafting.request import build, display_template, sample_group
from veyra_contracts.drafting.verify import verify
from veyra_contracts.library import library_match, load_library


class AnalyzeIn(BaseModel):
    source_id: str
    samples: list[str] = Field(min_length=1)
    mode: str | None = None
    source_name: str | None = None


def frame(kind: str, data: Any) -> bytes:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()


def split_samples(samples: list[str], limit: int) -> list[bytes]:
    """Entries with blank lines hold several multi-line events (C6)."""
    out: list[bytes] = []
    for entry in samples:
        for chunk in entry.replace("\r\n", "\n").split("\n\n"):
            if chunk.strip():
                out.append(chunk.strip("\n").encode("utf-8"))
    return out[:limit]


def analyze(ctx: AppContext, principal: Principal, body: AnalyzeIn) -> Iterator[bytes]:
    with DbSession(ctx.engine) as db:
        source = db.get(Source, body.source_id)
        assert source is not None
        tenant_id = source.tenant_id
    contract_id = contract_id_for(body.source_id)
    raws = split_samples(body.samples, ctx.cfg.onboarding_max_samples)
    try:
        layers = classify(raws[0].decode("utf-8", errors="replace"))
        yield frame("classification", {"layers": layers, "contract_id": contract_id})

        groups: dict[tuple, list[bytes]] = {}
        sigs: dict[tuple, str] = {}
        for raw in raws:
            text = peel(raw, layers).template_text
            key = sample_group(text)
            groups.setdefault(key, []).append(raw)
            # Scope is the future contract id (TC32). The sig is the first line's,
            # which is what the normalizer computes for that line.
            sigs.setdefault(key, template_sig(contract_id, text))
        prepared = {
            sigs[key]: build(group, template_sig=sigs[key], layers=layers)
            for key, group in groups.items()
        }
        counts = {sigs[key]: len(group) for key, group in groups.items()}
        yield frame(
            "templates",
            [
                {
                    "template_sig": sig,
                    "drain_template": display_template(item),
                    "count": counts[sig],
                    "tokens": item.request["tokens"],
                }
                for sig, item in prepared.items()
            ],
        )

        matches = library_match(
            raws, load_library(ctx.cfg.contracts_repo), threshold=ctx.cfg.library_match_min
        )
        matched = next((m.contract_id for m in matches if m.matched), None)
        yield frame(
            "library",
            {"matches": [m.to_dict() for m in matches[:3]], "matched": matched},
        )
        if matched is not None:
            yield frame("done", {"draft_id": None, "library": matched})
            return

        assert ctx.drafter is not None
        entries: list[dict[str, Any]] = []
        templates = []
        taken: set[str] = set()
        for sig, item in prepared.items():
            with ctx.draft_slots:
                outcome = ctx.drafter.draft(item, mode=body.mode)
            template = generalize(item, outcome.response, taken_ids=taken)
            taken.add(template.id)
            templates.append(template)
            entries.append(template_entry(item, outcome, template))
            yield frame(
                "draft",
                {
                    "template_sig": sig,
                    "source": outcome.source,
                    "pattern": template.pattern,
                    "latency_ms": outcome.latency_ms,
                },
            )

        draft_id = f"dr_{uuid7().hex}"
        yaml_text = new_contract(
            contract_id=contract_id,
            tenant_id=tenant_id,
            source_id=body.source_id,
            layers=layers,
            templates=templates,
            timezone=ctx.cfg.drafter_timezone,
            drafted_by=entries[0]["source"],
            draft_id=draft_id,
        )
        verification = verify(yaml_text, raws, {t.id for t in templates}, ctx=engine_context(ctx))
        with DbSession(ctx.engine) as db:
            db.add(
                Draft(
                    draft_id=draft_id,
                    tenant_id=tenant_id,
                    source_id=body.source_id,
                    contract_id=contract_id,
                    state="ready",
                    created_by=principal.email,
                    created_at=ctx.now(),
                    updated_at=ctx.now(),
                    payload_json=json.dumps(
                        {
                            "kind": "onboarding",
                            "layers": layers,
                            "raw_available": True,
                            "samples_b64": [base64.b64encode(r).decode() for r in raws],
                            "templates": entries,
                            "yaml": yaml_text,
                            "verification": verification.to_dict(),
                            "backtest": None,
                            "library": [m.to_dict() for m in matches[:3]],
                        }
                    ),
                )
            )
            db.commit()
        ctx.hub.publish(
            "draft",
            {
                "draft_id": draft_id,
                "drift_id": None,
                "state": "ready",
                "source_id": body.source_id,
            },
        )
        yield frame("done", {"draft_id": draft_id, "verification": verification.to_dict()})
    except Exception as exc:
        yield frame("error", {"message": f"{type(exc).__name__}: {exc}"})
