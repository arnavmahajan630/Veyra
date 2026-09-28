"""Everything the routes share, built once per process (and once per test)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlmodel import Session as DbSession
from sqlmodel import select

from control_api.audit import AuditLog
from control_api.contracts_repo import ensure_repo
from control_api.db import init_db, make_engine
from control_api.events import EventHub
from control_api.evidence import EventIndex, RawStore, ReplayWatcher
from control_api.inventory import rewrite_inventory
from control_api.keys import LastKeyMemory, ensure_pepper
from control_api.messages import republish_all
from control_api.publisher import ControlPublisher, ProducerLike, PublishError
from control_api.seed import seed
from control_api.seed_docs import SeedDocs, load_seed_docs
from control_api.tables import Tenant
from veyra_common.envelope import rfc3339_ns
from veyra_common.settings import Settings

Clock = Callable[[], int]  # nanoseconds since the epoch
Spawn = Callable[[Callable[[], None]], None]


def spawn_thread(work: Callable[[], None]) -> None:
    """Run background work (replay jobs) on a daemon thread; tests pass an inline spawn."""
    threading.Thread(target=work, daemon=True).start()


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
    producer: ProducerLike
    last_key: LastKeyMemory = field(default_factory=LastKeyMemory)
    # C2: backtest and replay collaborators (evidence.py). None = not configured.
    index: EventIndex | None = None
    raw: RawStore | None = None
    watcher: ReplayWatcher | None = None
    spawn: Spawn = spawn_thread
    # C3: tells the drift worker to forget its groups on /internal/reset
    drift_reset: Callable[[], None] | None = None

    def now(self) -> str:
        return rfc3339_ns(self.clock())


def build_context(
    cfg: Settings,
    producer: ProducerLike,
    *,
    clock: Clock = time.time_ns,
    docs: SeedDocs | None = None,
    index: EventIndex | None = None,
    raw: RawStore | None = None,
    watcher: ReplayWatcher | None = None,
    spawn: Spawn = spawn_thread,
    drift_reset: Callable[[], None] | None = None,
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
        producer=producer,
        index=index,
        raw=raw,
        watcher=watcher,
        spawn=spawn,
        drift_reset=drift_reset,
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


def publish_then_commit(
    ctx: AppContext, db: DbSession, items: list[tuple[str, BaseModel | None]]
) -> None:
    """Publish the changed IF-CONTROL keys, then commit; on a publish failure roll back
    and answer 503, so SQLite never gets ahead of ``control``."""
    try:
        ctx.publisher.publish_many(items)
    except PublishError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"control topic unavailable: {exc}") from exc
    db.commit()


def get_ctx(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


def get_db(ctx: AppContext = Depends(get_ctx)) -> Iterator[DbSession]:
    with DbSession(ctx.engine) as db:
        yield db
