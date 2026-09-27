"""Sessions, principals, role checks and tenant scoping; the /auth routes.

Scoping rule (C1): a non-platform user gets 404, never 403, for another tenant's
resources, so ids of other tenants don't leak. Role failures are 403.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session as DbSession

from control_api.context import AppContext, get_ctx, get_db
from control_api.security import new_session_token, token_digest, verify_password
from control_api.tables import PLATFORM, SessionRow, User
from veyra_common.envelope import rfc3339_ns

COOKIE = "veyra_session"
NS_PER_MINUTE = 60 * 1_000_000_000


@dataclass(frozen=True)
class Principal:
    email: str
    name: str
    role: str
    tenant_id: str

    @property
    def is_platform(self) -> bool:
        return self.tenant_id == PLATFORM

    def can_see(self, tenant_id: str) -> bool:
        return self.is_platform or self.tenant_id == tenant_id


def current_principal(
    request: Request,
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Principal:
    token = request.cookies.get(COOKIE)
    session = db.get(SessionRow, token_digest(token)) if token else None
    if session is None or session.expires_at <= ctx.now():
        raise HTTPException(status_code=401, detail="sign in required")
    user = db.get(User, session.email)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return Principal(email=user.email, name=user.name, role=user.role, tenant_id=user.tenant_id)


def require(*roles: str) -> Callable[..., Principal]:
    allowed = frozenset(roles)

    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"this action needs one of the roles: {', '.join(sorted(allowed))}",
            )
        return principal

    return dependency


def visible_or_404(principal: Principal, tenant_id: str, what: str) -> None:
    if not principal.can_see(tenant_id):
        raise HTTPException(status_code=404, detail=f"{what} not found")


class LoginBody(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    email: str
    name: str


class MeOut(BaseModel):
    user: UserOut
    role: str
    tenant: str
    demo_mode: bool


router = APIRouter(prefix="/auth", tags=["auth"])


def _me(email: str, name: str, role: str, tenant_id: str, ctx: AppContext) -> MeOut:
    return MeOut(
        user=UserOut(email=email, name=name),
        role=role,
        tenant=tenant_id,
        demo_mode=ctx.cfg.demo_mode,
    )


@router.post("/login", response_model=MeOut)
def login(
    body: LoginBody,
    response: Response,
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> MeOut:
    user = db.get(User, body.email)
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status_code=401, detail="wrong email or password")
    token = new_session_token()
    expires_at = rfc3339_ns(ctx.clock() + ctx.cfg.session_ttl_min * NS_PER_MINUTE)
    db.add(SessionRow(token_sha256=token_digest(token), email=user.email, expires_at=expires_at))
    ctx.audit.record(
        db,
        actor=user.email,
        role=user.role,
        action="auth.login",
        target=user.email,
        tenant_id=user.tenant_id,
    )
    db.commit()
    response.set_cookie(
        COOKIE,
        token,
        max_age=ctx.cfg.session_ttl_min * 60,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return _me(user.email, user.name, user.role, user.tenant_id, ctx)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    principal: Principal = Depends(current_principal),
    ctx: AppContext = Depends(get_ctx),
    db: DbSession = Depends(get_db),
) -> Response:
    token = request.cookies.get(COOKIE)
    session = db.get(SessionRow, token_digest(token)) if token else None
    if session is not None:
        db.delete(session)
    ctx.audit.record(
        db,
        actor=principal.email,
        role=principal.role,
        action="auth.logout",
        target=principal.email,
        tenant_id=principal.tenant_id,
    )
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response


@router.get("/me", response_model=MeOut)
def me(
    principal: Principal = Depends(current_principal), ctx: AppContext = Depends(get_ctx)
) -> MeOut:
    return _me(principal.email, principal.name, principal.role, principal.tenant_id, ctx)
