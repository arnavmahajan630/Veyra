"""The control-api FastAPI app (IF-API-CONTROL; Caddy mounts it at /api/control)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from control_api import auth, routes_sources, routes_tenants
from control_api.context import AppContext


def create_app(ctx: AppContext) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        ctx.hub.bind(asyncio.get_running_loop())
        yield
        ctx.publisher.flush()

    app = FastAPI(title="VEYRA control-api", version="0.1.0", lifespan=lifespan)
    app.state.ctx = ctx
    for router in (auth.router, routes_tenants.router, routes_sources.router):
        app.include_router(router)
    return app
