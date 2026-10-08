"""Local demo authentication. Only mounted/usable when AUTH_MODE=local-demo in a local/test environment."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_principal, get_session, load_principal
from app.api.schemas import DevLogin
from app.auth.principal import Principal
from app.auth.tokens import issue_token
from app.core.errors import Forbidden, Unauthenticated
from app.models import entities as m
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _demo_mode(request: Request) -> None:
    s = request.app.state.settings
    if s.auth_mode != "local-demo" or not s.is_local:
        raise Forbidden("Demo identities are only available in explicit local demo mode.")


def _principal_view(p: Principal) -> dict:
    return {"user_id": p.user_id, "username": p.username, "display_name": p.display_name, "team": p.team,
            "grants": [{"role": r.value, "scope_ids": sorted([s or "*" for s in scopes])}
                       for r, scopes in sorted(p.grants.items())]}


@router.get("/demo-users")
def demo_users(request: Request, session: Session = Depends(get_session)):
    _demo_mode(request)
    out = []
    for u in session.scalars(select(m.User).where(m.User.is_active.is_(True)).order_by(m.User.username)):
        out.append(_principal_view(load_principal(session, u.id)))
    return {"items": out, "mode": "local-demo",
            "warning": "Local demo identities. Production requires a real identity provider (OIDC/Entra ID)."}


@router.post("/dev-login")
def dev_login(payload: DevLogin, request: Request, session: Session = Depends(get_session)):
    _demo_mode(request)
    user = session.scalars(select(m.User).where(m.User.username == payload.username)).first()
    if user is None or not user.is_active:
        raise Unauthenticated("Unknown demo user")
    s = request.app.state.settings
    now = request.app.state.clock.now()
    principal = load_principal(session, user.id)
    audit.record_raw(session, principal=principal, now=now, action="auth.demo_login", object_type="user",
                     object_id=user.id)
    session.commit()
    return {"token": issue_token(user.id, s.secret_key, now, s.token_ttl_minutes), "token_type": "bearer",
            "user": _principal_view(principal)}


@router.get("/me")
def me(principal: Principal = Depends(get_principal)):
    return _principal_view(principal)
