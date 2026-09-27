"""Everything the routes share, built once per process (and once per test)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from fastapi import Depends, Request
from sqlalchemy.engine import Engine
from sqlmodel import Session as DbSession
from sqlmodel import select

from control_api.audit import AuditLog
from control_api.contracts_repo import ensure_repo
from control_api.db import init_db, make_engine
from control_api.events import EventHub
from control_api.inventory import rewrite_inventory
from control_api.keys import LastKeyMemory, ensure_pepper
from control_api.messages import republish_all
from control_api.publisher import ControlPublisher, ProducerLike
from control_api.seed import seed
from control_api.seed_docs import SeedDocs, load_seed_docs
from control_api.tables import Tenant
from veyra_common.envelope import rfc3339_ns
from veyra_common.settings import Settings

Clock = Callable[[], int]  # nanoseconds since the epoch


@dataclass
class AppContext:
    cfg: Settings
    engine: Engine
    publisher: ControlPublisher
    audit: AuditLog
    hub: EventHub
    docs: SeedDocs
    pepper: bytes
    pepper_id: str
    clock: Clock
    last_key: LastKeyMemory = field(default_factory=LastKeyMemory)

    def now(self) -> str:
        return rfc3339_ns(self.clock())


def build_context(
    cfg: Settings,
    producer: ProducerLike,
    *,
    clock: Clock = time.time_ns,
    docs: SeedDocs | None = None,
) -> AppContext:
    engine = make_engine(cfg.control_db)
    init_db(engine)
    pepper, pepper_id = ensure_pepper(cfg.keys_dir)
    return AppContext(
        cfg=cfg,
        engine=engine,
        publisher=ControlPublisher(producer, timeout_s=cfg.control_publish_timeout_s),
        audit=AuditLog(producer, clock),
        hub=EventHub(cfg.sse_queue_max),
        docs=docs or load_seed_docs(),
        pepper=pepper,
        pepper_id=pepper_id,
        clock=clock,
    )


def first_boot(ctx: AppContext) -> None:
    """Seed an empty database, then make `control` and the inventory complete (C1)."""
    ensure_repo(ctx.cfg.contracts_repo)
    with DbSession(ctx.engine) as db:
        if db.exec(select(Tenant)).first() is None:
            seed(db, cfg=ctx.cfg, now_ns=ctx.clock())
            db.commit()
        republish_all(db, ctx.publisher, ctx.docs, ctx.now())
        rewrite_inventory(db, ctx.cfg)


def get_ctx(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


def get_db(ctx: AppContext = Depends(get_ctx)) -> Iterator[DbSession]:
    with DbSession(ctx.engine) as db:
        yield db
