"""The contract registry (C2): versions, the lifecycle, four-eyes, and what each step publishes.

Lifecycle (IF-CONTRACT-YAML): ``draft → testing → canary → active → retired``.

- **submit** compiles, lints and golden-tests a version. A failure keeps it a ``draft``
  (with the reports) and commits nothing to git. A pass commits the YAML to the registry
  repository and moves it through ``testing`` to ``canary``, retiring any older canary
  (``superseded``).
- **approve** needs a role in ``APPROVERS`` and a principal who is **not the author**
  (the submitter): four-eyes, 403 otherwise.
- **promote** needs an approved canary; the old active version is retired.
- **rollback** restores a version that was active before.

These functions change rows, record transitions and audit, and return the IF-CONTROL
items to publish. They never commit: the route publishes, then commits
(``publish_then_commit``), so SQLite never gets ahead of ``control``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel
from sqlmodel import Session as DbSession
from sqlmodel import col, select

from control_api.auth import Principal
from control_api.backtest import drift_sigs, run_backtest
from control_api.context import AppContext
from control_api.contracts_repo import commit_all
from control_api.messages import contract_items, source_message
from control_api.tables import Contract, ContractTransition, ContractVersion, Source, Tenant
from veyra_common.models import control_key
from veyra_contracts import CompiledContract, ContractError, ContractYaml, compile
from veyra_contracts.golden import GoldenReport, run_golden
from veyra_contracts.lint import LintFinding, has_errors, lint
from veyra_engine import EngineContext

WRITERS = ("admin", "pack_author")
APPROVERS = ("admin", "pack_approver")
ACTORS = ("admin", "pack_author", "pack_approver")

Items = list[tuple[str, BaseModel | None]]


class RegistryError(Exception):
    """A request the registry refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


@dataclass
class Submission:
    version: ContractVersion
    golden: GoldenReport
    findings: list[LintFinding]
    items: Items


def engine_context(ctx: AppContext) -> EngineContext:
    """The engine settings the normalizer runs with, for golden tests and backtests."""
    return EngineContext(
        vocab={v.name: v.entries for v in ctx.docs.vocab},
        enrich={e.name: e.rows for e in ctx.docs.enrich},
        budget_us=ctx.cfg.engine_budget_us,
        peel_max_depth=ctx.cfg.peel_max_depth,
        max_event_bytes=ctx.cfg.max_event_bytes,
    )


def registry_file(root: Path, tenant_id: str, contract_id: str) -> Path:
    return root / tenant_id / f"{contract_id}.yaml"


def visible_contract(db: DbSession, principal: Principal, contract_id: str) -> Contract:
    contract = db.get(Contract, contract_id)
    if contract is None or not principal.can_see(contract.tenant_id):
        raise RegistryError(404, f"contract {contract_id} not found")
    return contract


def get_version(db: DbSession, contract_id: str, version: int) -> ContractVersion:
    row = db.get(ContractVersion, (contract_id, version))
    if row is None:
        raise RegistryError(404, f"{contract_id}@{version} not found")
    return row


def versions_of(db: DbSession, contract_id: str) -> list[ContractVersion]:
    query = select(ContractVersion).where(col(ContractVersion.contract_id) == contract_id)
    return list(db.exec(query.order_by(col(ContractVersion.version))).all())


def compiled_of(row: ContractVersion) -> dict[str, Any]:
    compiled: dict[str, Any] = json.loads(row.compiled_json or "{}")
    return compiled


def _transition(
    ctx: AppContext,
    db: DbSession,
    row: ContractVersion,
    action: str,
    from_state: str | None,
    actor: str,
    reason: str = "",
) -> None:
    db.add(
        ContractTransition(
            contract_id=row.contract_id,
            version=row.version,
            action=action,
            from_state=from_state,
            to_state=row.state,
            actor=actor,
            at=ctx.now(),
            reason=reason,
        )
    )


def _retire(ctx: AppContext, db: DbSession, row: ContractVersion, actor: str, reason: str) -> None:
    previous = row.state
    row.state = "retired"
    row.retired_reason = reason
    db.add(row)
    _transition(ctx, db, row, "retired", previous, actor, reason)


def _compile(yaml_text: str, compiled_at: str) -> CompiledContract:
    try:
        return compile(yaml_text, compiled_at=compiled_at)
    except ContractError as exc:
        raise RegistryError(
            422, {"message": exc.message, "line": exc.line, "column": exc.column}
        ) from exc


def _check_target(db: DbSession, principal: Principal, compiled: CompiledContract) -> None:
    if db.get(Tenant, compiled.tenant) is None or not principal.can_see(compiled.tenant):
        raise RegistryError(404, f"tenant {compiled.tenant} not found")
    for source_id in compiled.sources:
        source = db.get(Source, source_id)
        if source is None or source.tenant_id != compiled.tenant:
            raise RegistryError(422, f"{source_id} is not a source of {compiled.tenant}")


def _check_version(
    db: DbSession, contract: Contract | None, compiled: CompiledContract
) -> ContractVersion | None:
    """The draft row a resubmission replaces, or None for a new version."""
    if contract is None:
        if compiled.version != 1:
            raise RegistryError(409, f"{compiled.contract} is new; its first version is 1")
        return None
    if contract.tenant_id != compiled.tenant:
        raise RegistryError(409, f"contract id {compiled.contract} is taken")
    latest = max((v.version for v in versions_of(db, contract.id)), default=0)
    existing = db.get(ContractVersion, (contract.id, compiled.version))
    if existing is not None:
        if existing.state != "draft":
            raise RegistryError(
                409,
                f"{contract.id}@{compiled.version} already exists ({existing.state}); "
                f"submit version {latest + 1}",
            )
        return existing
    if compiled.version != latest + 1:
        raise RegistryError(409, f"the next version of {contract.id} is {latest + 1}")
    return None


def submit(
    ctx: AppContext,
    db: DbSession,
    principal: Principal,
    yaml_text: str,
    *,
    draft_id: str | None = None,
) -> Submission:
    now = ctx.now()
    compiled = _compile(yaml_text, now)  # TC1: compiled_at is the version's creation time
    _check_target(db, principal, compiled)
    contract = db.get(Contract, compiled.contract)
    existing = _check_version(db, contract, compiled)

    spec = ContractYaml.model_validate(yaml.safe_load(yaml_text))
    findings = lint(spec, compiled)
    golden = run_golden(spec, compiled, ctx.cfg.contracts_repo, ctx=engine_context(ctx))
    passed = golden.passed and not has_errors(findings)

    if contract is None:
        contract = Contract(id=compiled.contract, tenant_id=compiled.tenant)
        db.add(contract)
        db.flush()  # parent first (tables.py)
    row = existing or ContractVersion(
        contract_id=compiled.contract,
        version=compiled.version,
        state="draft",
        yaml=yaml_text,
        author=principal.email,
        created_at=now,
    )
    row.state = "draft"
    row.yaml = yaml_text
    row.compiled_json = compiled.to_json().decode("utf-8")
    row.author = principal.email
    row.created_at = now
    row.draft_id = draft_id
    row.golden_report_json = json.dumps(golden.to_dict())
    row.lint_json = json.dumps([f.to_dict() for f in findings])
    row.approved_by = row.approved_at = row.git_commit = None
    db.add(row)
    _transition(ctx, db, row, "submitted", None, principal.email)

    items: Items = []
    if passed:
        path = registry_file(ctx.cfg.contracts_repo, compiled.tenant, compiled.contract)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_text, encoding="utf-8", newline="\n")
        row.git_commit = commit_all(
            ctx.cfg.contracts_repo,
            f"{compiled.contract}@{compiled.version}: submitted by {principal.email}",
            author=principal.email,
        )
        row.state = "testing"
        _transition(ctx, db, row, "tested", "draft", principal.email)
        if contract.canary_version is not None and contract.canary_version != row.version:
            old = get_version(db, contract.id, contract.canary_version)
            _retire(ctx, db, old, principal.email, f"superseded by v{row.version}")
        row.state = "canary"
        contract.canary_version = row.version
        db.add(contract)
        _transition(ctx, db, row, "canary", "testing", principal.email)
        items = list(contract_items(db, contract, now))
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="contract.submit",
        target=f"{row.contract_id}@{row.version}", detail=f"state={row.state}",
        tenant_id=contract.tenant_id,
    )  # fmt: skip
    return Submission(version=row, golden=golden, findings=findings, items=items)


def approve(
    ctx: AppContext, db: DbSession, principal: Principal, contract_id: str, version: int
) -> ContractVersion:
    contract = visible_contract(db, principal, contract_id)
    row = get_version(db, contract_id, version)
    if row.state != "canary":
        raise RegistryError(
            409, f"{contract_id}@{version} is {row.state}; only a canary can be approved"
        )
    if principal.email == row.author:
        raise RegistryError(
            403,
            f"four-eyes: {principal.email} submitted {contract_id}@{version}, "
            "so someone else must approve it",
        )
    if row.approved_by is None:
        row.approved_by = principal.email
        row.approved_at = ctx.now()
        db.add(row)
        _transition(ctx, db, row, "approved", "canary", principal.email)
        ctx.audit.record(
            db, actor=principal.email, role=principal.role, action="contract.approve",
            target=f"{contract_id}@{version}", tenant_id=contract.tenant_id,
        )  # fmt: skip
    return row


def _bind_sources(db: DbSession, row: ContractVersion) -> Items:
    """Point every source the version declares at this contract (``sources:`` wins)."""
    items: Items = []
    for source_id in compiled_of(row).get("sources", []):
        source = db.get(Source, source_id)
        if source is not None and source.contract_id != row.contract_id:
            source.contract_id = row.contract_id
            db.add(source)
            items.append((control_key("source", source.id), source_message(source)))
    return items


def promote(
    ctx: AppContext, db: DbSession, principal: Principal, contract_id: str, version: int
) -> tuple[ContractVersion, Items]:
    contract = visible_contract(db, principal, contract_id)
    row = get_version(db, contract_id, version)
    if row.state != "canary":
        raise RegistryError(
            409, f"{contract_id}@{version} is {row.state}; only a canary can be promoted"
        )
    if row.approved_by is None:
        raise RegistryError(409, f"approve {contract_id}@{version} first (four-eyes)")
    previous = contract.active_version
    if previous is not None:
        _retire(ctx, db, get_version(db, contract_id, previous), principal.email,
                f"replaced by v{version}")  # fmt: skip
    row.state = "active"
    row.promoted_by = principal.email
    row.promoted_at = ctx.now()
    db.add(row)
    _transition(ctx, db, row, "promoted", "canary", principal.email)
    contract.active_version = version
    contract.canary_version = None
    db.add(contract)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="contract.promote",
        target=f"{contract_id}@{version}",
        detail=f"replaces v{previous}" if previous is not None else "first active version",
        tenant_id=contract.tenant_id,
    )  # fmt: skip
    items: Items = list(contract_items(db, contract, ctx.now()))
    return row, items + _bind_sources(db, row)


def rollback(
    ctx: AppContext, db: DbSession, principal: Principal, contract_id: str, to_version: int
) -> tuple[ContractVersion, Items]:
    contract = visible_contract(db, principal, contract_id)
    target = get_version(db, contract_id, to_version)
    if contract.active_version == to_version:
        raise RegistryError(409, f"{contract_id}@{to_version} is already active")
    if target.promoted_at is None or target.state != "retired":
        raise RegistryError(
            409, f"{contract_id}@{to_version} was never active; only a retired active can return"
        )
    previous = contract.active_version
    if previous is not None:
        _retire(ctx, db, get_version(db, contract_id, previous), principal.email,
                f"rolled back to v{to_version}")  # fmt: skip
    target.state = "active"
    target.retired_reason = None
    db.add(target)
    _transition(ctx, db, target, "rolled_back", "retired", principal.email,
                f"from v{previous}")  # fmt: skip
    contract.active_version = to_version
    db.add(contract)
    ctx.audit.record(
        db, actor=principal.email, role=principal.role, action="contract.rollback",
        target=f"{contract_id}@{to_version}", detail=f"from v{previous}",
        tenant_id=contract.tenant_id,
    )  # fmt: skip
    items: Items = list(contract_items(db, contract, ctx.now()))
    return target, items + _bind_sources(db, target)


def backtest(
    ctx: AppContext,
    db: DbSession,
    contract: Contract,
    row: ContractVersion,
    *,
    sigs: list[str] | None = None,
    samples: list[str] | None = None,
) -> dict[str, Any]:
    """Backtest ``row`` against the active version and store the result on the row.

    Events: the requested sigs plus every open drift sig on the contract's sources (the
    shapes a new version is usually written for), capped at ``VEYRA_BACKTEST_MAX``.
    """
    candidate = compiled_of(row)
    active_row = (
        db.get(ContractVersion, (contract.id, contract.active_version))
        if contract.active_version not in (None, row.version)
        else None
    )
    wanted = list(dict.fromkeys([*(sigs or []), *drift_sigs(db, candidate.get("sources", []))]))
    result = run_backtest(
        compiled_of(active_row) if active_row is not None else None,
        candidate,
        index=ctx.index,
        raw=ctx.raw,
        sigs=wanted,
        limit=ctx.cfg.backtest_max,
        samples=(samples or [])[: ctx.cfg.backtest_max],
    )
    row.backtest_json = json.dumps(result)
    db.add(row)
    return result
