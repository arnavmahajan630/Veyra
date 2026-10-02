"""The pre-demo world (04_DEMO_SCRIPT §2, C1 "Seed") and the reset that returns to it.

python -m control_api.seed --scenario sih_main [--no-publish]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import cast

from sqlalchemy.engine import Engine
from sqlmodel import Session as DbSession
from sqlmodel import select

from control_api.contracts_repo import ensure_repo, head_commit, reset_to_seed
from control_api.db import init_db, make_engine, reset_db
from control_api.inventory import rewrite_inventory
from control_api.messages import republish_all
from control_api.publisher import ControlPublisher, ProducerLike
from control_api.security import hash_password
from control_api.seed_docs import load_seed_docs
from control_api.tables import (
    PLATFORM,
    Contract,
    ContractVersion,
    SessionRow,
    Source,
    Tenant,
    User,
)
from veyra_common.envelope import rfc3339_ns
from veyra_common.kafka import make_producer
from veyra_common.settings import Settings, settings
from veyra_contracts import compile as compile_contract

SCENARIOS = frozenset({"sih_main"})
LIBRARY_TENANTS = ("t_ntro_core",)
TENANTS = (("t_ntro_core", "NTRO Core Ops"), ("t_maha_power", "Maha Power Corp"))
USERS = (
    ("admin@veyra", "Admin", "admin", PLATFORM),
    ("author@maha", "Maha author", "pack_author", "t_maha_power"),
    ("approver@veyra", "Approver", "pack_approver", PLATFORM),
)
# The Maha Power auth server is NOT seeded: it is onboarded live in Beat 2.
SOURCES: tuple[dict[str, str], ...] = (
    {
        "id": "src_fw_dmz_01",
        "tenant_id": "t_ntro_core",
        "name": "Acme NGFW (DMZ)",
        "vendor": "acme_ngfw",
        "zone": "dmz",
        "transport": "syslog_tcp",
        "listener": "dmz-tcp",
        "match_kind": "syslog_host",
        "match_value": "fw-dmz-01",
        "contract_id": "acme_ngfw_cef",
    },
    {
        "id": "src_lnx_core_07",
        "tenant_id": "t_ntro_core",
        "name": "Linux sshd (core)",
        "vendor": "linux",
        "zone": "core",
        "transport": "syslog_udp",
        "listener": "core-udp",
        "match_kind": "syslog_host",
        "match_value": "core-lnx-07",
        "contract_id": "linux_sshd",
    },
)


@dataclass(frozen=True)
class SeedSummary:
    tenants: int
    users: int
    sources: int
    contracts: int


def _check_scenario(scenario: str) -> None:
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; known: {sorted(SCENARIOS)}")


def seed(db: DbSession, *, cfg: Settings, now_ns: int, scenario: str = "sih_main") -> SeedSummary:
    """Insert the scenario's rows into an empty database. The caller commits."""
    _check_scenario(scenario)
    at = rfc3339_ns(now_ns)
    for tenant_id, name in TENANTS:
        db.add(Tenant(id=tenant_id, name=name, created_at=at))
    db.flush()  # parents first: inserts aren't ordered across tables (see tables.py)
    for email, name, role, tenant_id in USERS:
        db.add(
            User(
                email=email,
                name=name,
                password_hash=hash_password(cfg.demo_password),
                role=role,
                tenant_id=tenant_id,
            )
        )
    for fields in SOURCES:
        db.add(Source.model_validate({**fields, "created_at": at}))

    commit = head_commit(cfg.contracts_repo)
    contracts = 0
    for tenant in LIBRARY_TENANTS:
        for path in sorted((cfg.contracts_repo / tenant).glob("*.yaml")):
            text = path.read_text(encoding="utf-8")
            compiled = compile_contract(text, compiled_at=at)
            db.add(
                Contract(
                    id=compiled.contract,
                    tenant_id=compiled.tenant,
                    active_version=compiled.version,
                )
            )
            db.flush()
            db.add(
                ContractVersion(
                    contract_id=compiled.contract,
                    version=compiled.version,
                    state="active",
                    yaml=text,
                    compiled_json=compiled.to_json().decode("utf-8"),
                    author="library",
                    approved_by="admin@veyra",
                    git_commit=commit,
                    created_at=at,
                )
            )
            contracts += 1
    return SeedSummary(
        tenants=len(TENANTS), users=len(USERS), sources=len(SOURCES), contracts=contracts
    )


def reset_state(
    engine: Engine, cfg: Settings, now_ns: int, scenario: str = "sih_main"
) -> SeedSummary:
    """Wipe SQLite, reset contracts-repo to `seed`, reseed. Publishing is the caller's job.

    Live sessions are carried across the wipe. ``reset_db`` drops every table, sessions
    included, so a reset used to sign the presenter out: the console's next request after
    Shift+R got a 401 and bounced to the login page, in the middle of a beat. Sessions are
    keyed by email and the seeded users keep their emails, so the rows can simply be put back.
    """
    _check_scenario(scenario)
    ensure_repo(cfg.contracts_repo)
    reset_to_seed(cfg.contracts_repo)
    with DbSession(engine) as db:
        live_sessions = [
            SessionRow(token_sha256=row.token_sha256, email=row.email, expires_at=row.expires_at)
            for row in db.exec(select(SessionRow)).all()
        ]
    reset_db(engine)
    with DbSession(engine) as db:
        summary = seed(db, cfg=cfg, now_ns=now_ns, scenario=scenario)
        emails = {user.email for user in db.exec(select(User)).all()}
        for session in live_sessions:
            # A session for a user the seed does not create cannot be restored; that user no
            # longer exists, so a 401 is the right answer for it.
            if session.email in emails:
                db.add(session)
        db.commit()
    return summary


def main(argv: Sequence[str] | None = None, cfg: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m control_api.seed", description="Reset control-api to a seeded scenario."
    )
    parser.add_argument("--scenario", default="sih_main")
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="skip Kafka: rebuild SQLite, contracts-repo and the inventory only",
    )
    args = parser.parse_args(argv)
    s = cfg or settings
    engine = make_engine(s.control_db)
    init_db(engine)
    summary = reset_state(engine, s, time.time_ns(), args.scenario)
    with DbSession(engine) as db:
        rewrite_inventory(db, s)
        if not args.no_publish:
            # confluent_kafka.Producer's produce() is a structural superset of ProducerLike
            # (extra keyword params with defaults); the cast documents that compatibility.
            producer = cast(ProducerLike, make_producer(cfg=s))
            publisher = ControlPublisher(producer, timeout_s=s.control_publish_timeout_s)
            republish_all(db, publisher, load_seed_docs(), rfc3339_ns())
    engine.dispose()
    print(json.dumps(asdict(summary)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
