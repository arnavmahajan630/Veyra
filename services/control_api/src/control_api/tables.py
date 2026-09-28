"""SQLite tables for the control plane.

C1 owns tenants, users, sessions, sources, api_keys and audit. contracts and
contract_versions carry the columns C2 lists; drafts, drift_items and replay_jobs are
placeholders that C2, C3 and C4 fill in.

Foreign keys are enforced (PRAGMA foreign_keys=ON), but there are no relationship()s, so
SQLAlchemy does NOT order inserts across tables within one flush. When a parent and its
child are added in the same transaction, call ``db.flush()`` after the parent.
"""

from __future__ import annotations

from sqlmodel import Field, SQLModel

PLATFORM = "*"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: str = Field(primary_key=True)
    name: str
    created_at: str


class User(SQLModel, table=True):
    __tablename__ = "users"

    email: str = Field(primary_key=True)
    name: str
    password_hash: str
    role: str
    tenant_id: str  # a tenant id, or PLATFORM


class SessionRow(SQLModel, table=True):
    __tablename__ = "sessions"

    token_sha256: str = Field(primary_key=True)
    email: str = Field(foreign_key="users.email", index=True)
    expires_at: str


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: str = Field(primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    name: str
    vendor: str
    zone: str
    transport: str  # syslog_udp | syslog_tcp | http_push
    listener: str | None = None
    match_kind: str | None = None  # peer_ip | syslog_host
    match_value: str | None = None
    contract_id: str | None = None
    expected_eps: float = 0.0
    salt_buckets: int = 1
    status: str = "active"
    created_at: str


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    key_id: str = Field(primary_key=True)
    source_id: str = Field(foreign_key="sources.id", index=True)
    tenant_id: str = Field(index=True)
    secret_sha256: str
    pepper_id: str
    quota_eps: int
    status: str = "active"
    created_by: str
    created_at: str
    revoked_at: str | None = None


class Contract(SQLModel, table=True):
    __tablename__ = "contracts"

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    active_version: int | None = None
    canary_version: int | None = None


class ContractVersion(SQLModel, table=True):
    __tablename__ = "contract_versions"

    contract_id: str = Field(primary_key=True, foreign_key="contracts.id")
    version: int = Field(primary_key=True)
    state: str
    yaml: str
    compiled_json: str | None = None
    author: str
    approved_by: str | None = None
    golden_report_json: str | None = None
    backtest_json: str | None = None
    git_commit: str | None = None
    created_at: str
    draft_id: str | None = None
    # C2 (migrations 1-5)
    lint_json: str | None = None
    approved_at: str | None = None
    promoted_by: str | None = None
    promoted_at: str | None = None
    retired_reason: str | None = None


class ContractTransition(SQLModel, table=True):
    """One step of a version's lifecycle, for the console's timeline (C6).

    ``action`` is what happened: submitted, tested, canary, approved, promoted, retired,
    rolled_back. ``from_state``/``to_state`` are equal for an approval (no state change).
    """

    __tablename__ = "contract_transitions"

    id: int | None = Field(default=None, primary_key=True)
    contract_id: str = Field(index=True)
    version: int
    action: str
    from_state: str | None = None
    to_state: str
    actor: str
    at: str
    reason: str = ""


class Draft(SQLModel, table=True):
    __tablename__ = "drafts"

    draft_id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    source_id: str | None = None
    drift_id: str | None = None
    state: str = "drafting"
    payload_json: str = "{}"
    created_at: str
    # C4 (migrations 12-15)
    contract_id: str | None = None
    created_by: str | None = None
    updated_at: str | None = None
    detail: str = ""


class DriftItem(SQLModel, table=True):
    __tablename__ = "drift_items"

    drift_id: str = Field(primary_key=True)
    source_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    template_sig: str = Field(index=True)
    related_sigs_json: str = "[]"
    drain_template: str = ""
    count: int = 0
    first_seen: str
    last_seen: str
    samples_masked_json: str = "[]"
    state: str = "open"
    draft_id: str | None = None
    # C3 (migrations 9-11)
    sample_event_uids_json: str = "[]"
    resolved_by: str | None = None  # "<contract>@<version>" whose active version covers it
    updated_at: str | None = None


class ReplayJob(SQLModel, table=True):
    __tablename__ = "replay_jobs"

    job_id: str = Field(primary_key=True)
    contract_id: str
    tenant_id: str = Field(index=True)
    state: str = "pending"
    total: int = 0
    published: int = 0
    normalized: int = 0
    params_json: str = "{}"
    created_at: str
    # C2 (migrations 6-8)
    created_by: str | None = None
    finished_at: str | None = None
    detail: str = ""


class AuditRow(SQLModel, table=True):
    """Mirror of the IF-AUDIT topic for fast console reads, plus the tenant for scoping."""

    __tablename__ = "audit"

    id: int | None = Field(default=None, primary_key=True)
    actor: str
    role: str
    action: str
    target: str
    detail: str = ""
    at: str = Field(index=True)
    tenant_id: str = Field(default=PLATFORM, index=True)
