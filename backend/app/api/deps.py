from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.auth.tokens import verify_token
from app.core import db
from app.core.errors import Unauthenticated
from app.models import entities as m
from app.models.enums import Role
from app.services.context import RequestContext


def get_session() -> Iterator[Session]:
    if db._session_factory is None:  # noqa: SLF001
        raise RuntimeError("database not initialised")
    session = db._session_factory()  # noqa: SLF001
    try:
        yield session
    finally:
        # Commands commit explicitly. Anything left uncommitted (errors, reads) is rolled back.
        session.rollback()
        session.close()


def load_principal(session: Session, user_id: str) -> Principal:
    user = session.get(m.User, user_id)
    if user is None or not user.is_active:
        raise Unauthenticated("Unknown or inactive user")
    grants: dict[Role, set[str | None]] = {}
    for ra in session.scalars(select(m.RoleAssignment).where(m.RoleAssignment.user_id == user.id)):
        grants.setdefault(Role(ra.role), set()).add(ra.scope_id)
    return Principal(user_id=user.id, username=user.username, display_name=user.display_name, team=user.team,
                     grants=grants)


def get_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise Unauthenticated("Missing bearer token")
    token = header.split(" ", 1)[1].strip()
    settings = request.app.state.settings
    user_id = verify_token(token, settings.secret_key, request.app.state.clock.now())
    principal = load_principal(session, user_id)
    request.state.principal = principal
    return principal


def get_ctx(request: Request, session: Session = Depends(get_session),
            principal: Principal = Depends(get_principal)) -> RequestContext:
    return RequestContext(session=session, principal=principal, clock=request.app.state.clock,
                          settings=request.app.state.settings)


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=100_000)):
        self.limit = limit
        self.offset = offset


def paged(items: list, total: int, page: Page) -> dict:
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}
