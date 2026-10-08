from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import Page, get_ctx, paged
from app.api.schemas import (
    ControlCreate,
    ControlRevisionUpdate,
    ImplementationCreate,
    ImplementationRevisionCreate,
    ImplementationRevisionUpdate,
    LockedCommand,
    NewRevision,
    ReasonCommand,
)
from app.models import entities as m
from app.providers.registry import all_providers
from app.services import control_detail, controls, implementations
from app.services.context import RequestContext
from app.services.views import control_revision_view, implementation_revision_view, validation_view

router = APIRouter(tags=["controls"])


@router.get("/controls")
def list_controls(page: Page = Depends(), ctx: RequestContext = Depends(get_ctx)):
    total = ctx.session.scalar(select(func.count()).select_from(m.Control))
    rows = ctx.session.scalars(select(m.Control).order_by(m.Control.id).limit(page.limit).offset(page.offset))
    items = []
    for c in rows:
        latest = c.revisions[-1] if c.revisions else None
        items.append({"id": c.id, "origin": c.origin, "operational_owner": c.operational_owner,
                      "name": latest.name if latest else None, "severity": latest.severity if latest else None,
                      "providers": latest.providers if latest else [], "latest_revision": latest.revision if latest else None,
                      "latest_status": latest.status if latest else None,
                      "implementations": [{"id": i.id, "name": i.name, "provider": i.provider}
                                          for i in c.implementations]})
    return paged(items, total, page)


@router.post("/controls", status_code=201)
def create_control(payload: ControlCreate, ctx: RequestContext = Depends(get_ctx)):
    c = controls.create_control(ctx, payload)
    ctx.commit()
    return control_detail.control_detail(ctx, c.id)


@router.get("/controls/{control_id}")
def get_control(control_id: str, ctx: RequestContext = Depends(get_ctx)):
    return control_detail.control_detail(ctx, control_id)


@router.post("/controls/{control_id}/revisions", status_code=201)
def new_control_revision(control_id: str, payload: NewRevision, ctx: RequestContext = Depends(get_ctx)):
    rev = controls.new_revision(ctx, control_id, payload.change_reason)
    ctx.commit()
    return control_revision_view(rev)


@router.put("/control-revisions/{revision_id}")
def update_control_revision(revision_id: str, payload: ControlRevisionUpdate, ctx: RequestContext = Depends(get_ctx)):
    rev = controls.update_draft(ctx, revision_id, payload)
    ctx.commit()
    return control_revision_view(rev)


@router.post("/control-revisions/{revision_id}/submit")
def submit_control_revision(revision_id: str, payload: LockedCommand, ctx: RequestContext = Depends(get_ctx)):
    rev = controls.submit_revision(ctx, revision_id, payload.expected_lock_version)
    ctx.commit()
    return control_revision_view(rev)


@router.delete("/control-revisions/{revision_id}")
def delete_control_revision(revision_id: str, ctx: RequestContext = Depends(get_ctx)):
    out = controls.delete_draft(ctx, revision_id)
    ctx.commit()
    return out


@router.post("/controls/{control_id}/retire")
def retire_control(control_id: str, payload: ReasonCommand, ctx: RequestContext = Depends(get_ctx)):
    controls.retire_control(ctx, control_id, payload.reason)
    ctx.commit()
    return control_detail.control_detail(ctx, control_id)


@router.post("/controls/{control_id}/implementations", status_code=201)
def create_implementation(control_id: str, payload: ImplementationCreate, ctx: RequestContext = Depends(get_ctx)):
    impl = implementations.create_implementation(ctx, control_id, payload)
    ctx.commit()
    return {"id": impl.id, "revision": implementation_revision_view(impl.revisions[-1])}


@router.get("/implementations/{impl_id}")
def get_implementation(impl_id: str, ctx: RequestContext = Depends(get_ctx)):
    impl = implementations.get_implementation(ctx, impl_id)
    validations = {}
    for r in impl.revisions:
        v = implementations.latest_validation(ctx, r)
        validations[r.id] = validation_view(v) if v else None
    history = {}
    for r in impl.revisions:
        history[r.id] = [validation_view(v) for v in ctx.session.scalars(
            select(m.ImplementationValidation).where(m.ImplementationValidation.implementation_revision_id == r.id)
            .order_by(m.ImplementationValidation.seq.desc()))]
    return {"id": impl.id, "control_id": impl.control_id, "provider": impl.provider, "name": impl.name,
            "mechanism_role": impl.mechanism_role, "management": impl.management,
            "revisions": [implementation_revision_view(r) for r in impl.revisions],
            "current_validation": validations, "validation_history": history}


@router.post("/implementations/{impl_id}/revisions", status_code=201)
def new_implementation_revision(impl_id: str, payload: ImplementationRevisionCreate,
                                ctx: RequestContext = Depends(get_ctx)):
    rev = implementations.new_revision(ctx, impl_id, payload)
    ctx.commit()
    return implementation_revision_view(rev)


@router.get("/implementation-revisions/{rev_id}")
def get_implementation_revision(rev_id: str, ctx: RequestContext = Depends(get_ctx)):
    rev = implementations.get_revision(ctx, rev_id)
    v = implementations.latest_validation(ctx, rev)
    return {**implementation_revision_view(rev), "revision_digest": implementations.revision_digest(rev),
            "latest_validation": validation_view(v) if v else None}


@router.put("/implementation-revisions/{rev_id}")
def update_implementation_revision(rev_id: str, payload: ImplementationRevisionUpdate,
                                   ctx: RequestContext = Depends(get_ctx)):
    rev = implementations.update_draft(ctx, rev_id, payload)
    ctx.commit()
    return implementation_revision_view(rev)


@router.post("/implementation-revisions/{rev_id}/validate")
def validate_implementation_revision(rev_id: str, ctx: RequestContext = Depends(get_ctx)):
    v = implementations.validate_revision(ctx, rev_id)
    ctx.commit()
    return validation_view(v)


@router.post("/implementation-revisions/{rev_id}/submit")
def submit_implementation_revision(rev_id: str, payload: LockedCommand, ctx: RequestContext = Depends(get_ctx)):
    rev = implementations.submit_revision(ctx, rev_id, payload.expected_lock_version)
    ctx.commit()
    return implementation_revision_view(rev)


@router.delete("/implementation-revisions/{rev_id}")
def delete_implementation_revision(rev_id: str, ctx: RequestContext = Depends(get_ctx)):
    out = implementations.delete_draft(ctx, rev_id)
    ctx.commit()
    return out


@router.get("/providers/capabilities")
def capabilities(ctx: RequestContext = Depends(get_ctx)):
    return {"items": [p.capabilities().to_dict() | {"prerequisites": p.readiness_prerequisites()}
                      for p in all_providers()]}
