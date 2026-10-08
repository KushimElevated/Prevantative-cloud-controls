from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import Page, get_ctx, paged
from app.api.schemas import ExceptionCreate, ExceptionDecision, ExceptionRenewal, LockedCommand, PlanStateCommand
from app.models import entities as m
from app.services import exceptions, settings
from app.services.context import RequestContext

router = APIRouter(tags=["exceptions"])


def _view(ctx: RequestContext, e: m.SecurityException) -> dict:
    warn = settings.get(ctx.session, "expiry")["warning_days"]
    return exceptions.exception_view(e, ctx.now, warn, exceptions.decisions_for(ctx, e.id))


@router.get("/exceptions")
def list_exceptions(page: Page = Depends(), control_id: str | None = None, effective_status: str | None = None,
                    disposition: str | None = None, expiring: bool = Query(False),
                    native_cleanup_pending: bool = Query(False), ctx: RequestContext = Depends(get_ctx)):
    q = select(m.SecurityException).order_by(m.SecurityException.expires_at, m.SecurityException.id)
    if control_id:
        q = q.where(m.SecurityException.control_id == control_id)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    views = [_view(ctx, e) for e in ctx.session.scalars(q) if readable is None or e.scope_id in readable]
    if effective_status:
        views = [v for v in views if v["effective_status"] == effective_status]
    if disposition:
        views = [v for v in views if v["disposition"] == disposition]
    if expiring:
        views = [v for v in views if v["expiring_soon"]]
    if native_cleanup_pending:
        views = [v for v in views if v["native_cleanup_pending"]]
    return paged(views[page.offset:page.offset + page.limit], len(views), page)


@router.post("/exceptions", status_code=201)
def request_exception(payload: ExceptionCreate, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.request_exception(ctx, payload)
    ctx.commit()
    return _view(ctx, e)


@router.get("/exceptions/{exc_id}")
def get_exception(exc_id: str, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.get_exception(ctx, exc_id)
    lineage = [exceptions.exception_view(x, ctx.now) for x in ctx.session.scalars(
        select(m.SecurityException).where(m.SecurityException.lineage_id == e.lineage_id)
        .order_by(m.SecurityException.revision)) if ctx.principal.can_read_scope(x.scope_id, ctx.tree)]
    return {**_view(ctx, e), "lineage": lineage}


@router.post("/exceptions/{exc_id}/start-review")
def start_review(exc_id: str, payload: LockedCommand, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.start_review(ctx, exc_id, payload.expected_lock_version)
    ctx.commit()
    return _view(ctx, e)


@router.post("/exceptions/{exc_id}/decision")
def decide(exc_id: str, payload: ExceptionDecision, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.decide(ctx, exc_id, payload.decision, payload.rationale, payload.expected_lock_version)
    ctx.commit()
    return _view(ctx, e)


@router.post("/exceptions/{exc_id}/revoke")
def revoke(exc_id: str, payload: PlanStateCommand, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.revoke(ctx, exc_id, payload.reason, payload.expected_lock_version)
    ctx.commit()
    return _view(ctx, e)


@router.post("/exceptions/{exc_id}/renew", status_code=201)
def renew(exc_id: str, payload: ExceptionRenewal, ctx: RequestContext = Depends(get_ctx)):
    e = exceptions.renew(ctx, exc_id, payload)
    ctx.commit()
    return _view(ctx, e)
