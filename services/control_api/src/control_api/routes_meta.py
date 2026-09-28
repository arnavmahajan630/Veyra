"""Read-only documents the console needs (IF-API-CONTROL ``GET /routes``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from control_api.auth import Principal, current_principal
from control_api.context import AppContext, get_ctx

router = APIRouter(tags=["meta"])


@router.get("/routes")
def routes(
    _principal: Principal = Depends(current_principal), ctx: AppContext = Depends(get_ctx)
) -> dict[str, Any]:
    return ctx.docs.routes.model_dump()
