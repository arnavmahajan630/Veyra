"""GET /stream: control-side SSE for the console (overview ticks come from evidence-api)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from control_api.auth import Principal, current_principal
from control_api.context import AppContext, get_ctx
from control_api.events import sse_stream

router = APIRouter(tags=["stream"])


@router.get("/stream")
async def stream(
    request: Request,
    principal: Principal = Depends(current_principal),
    ctx: AppContext = Depends(get_ctx),
) -> StreamingResponse:
    # A tenant-pinned user gets only their tenant's events; a platform user gets everything.
    tenant = None if principal.is_platform else principal.tenant_id
    return StreamingResponse(
        sse_stream(ctx.hub, ctx.cfg.sse_heartbeat_s, request.is_disconnected, tenant),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
