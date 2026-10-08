from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.deps import Page, get_ctx, paged
from app.api.schemas import AssessmentCreate, ReadinessEvidenceCreate, WorkReferenceCreate
from app.auth.principal import require_any_role
from app.core.errors import Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import Role
from app.providers.registry import get_provider
from app.services import assessments, audit
from app.services.context import RequestContext
from app.services.scopes import scope_summary
from app.services.views import (
    binding_view,
    evidence_view,
    resource_view,
    result_view,
    run_view,
    snapshot_view,
    work_view,
)

router = APIRouter(tags=["inventory", "assessments"])


@router.get("/scopes")
def list_scopes(provider: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    tree = ctx.tree
    readable = ctx.principal.readable_scope_ids(tree)
    items = []
    for sid in sorted(tree.scopes):
        s = tree.scopes[sid]
        if provider and s.provider != provider:
            continue
        if readable is not None and sid not in readable:
            continue
        items.append({**scope_summary(s), "path": tree.path(sid), "children": tree.children.get(sid, [])})
    return {"items": items, "total": len(items)}


@router.get("/scopes/{scope_id}")
def get_scope(scope_id: str, ctx: RequestContext = Depends(get_ctx)):
    if not ctx.principal.can_read_scope(scope_id, ctx.tree):
        raise NotFound(f"Scope {scope_id} not found")
    s = ctx.tree.get(scope_id)
    return {**scope_summary(s), "ancestors": ctx.tree.ancestors(scope_id),
            "descendants": ctx.tree.descendants(scope_id, include_self=False), "path": ctx.tree.path(scope_id)}


@router.get("/inventory/snapshots")
def list_snapshots(ctx: RequestContext = Depends(get_ctx)):
    out = []
    for s in ctx.session.scalars(select(m.InventorySnapshot).order_by(m.InventorySnapshot.collected_at.desc())):
        n = ctx.session.scalar(select(func.count()).select_from(m.ResourceSnapshot).where(
            m.ResourceSnapshot.snapshot_id == s.id))
        out.append(snapshot_view(s, n))
    return {"items": out, "total": len(out)}


@router.get("/inventory/snapshots/{snapshot_id}/resources")
def list_resources(snapshot_id: str, page: Page = Depends(), resource_type: str | None = None,
                   ctx: RequestContext = Depends(get_ctx)):
    if ctx.session.get(m.InventorySnapshot, snapshot_id) is None:
        raise NotFound("Snapshot not found")
    q = select(m.ResourceSnapshot).where(m.ResourceSnapshot.snapshot_id == snapshot_id)
    if resource_type:
        q = q.where(m.ResourceSnapshot.resource_type == resource_type)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    if readable is not None:
        q = q.where(m.ResourceSnapshot.scope_id.in_(readable))
    total = ctx.session.scalar(select(func.count()).select_from(q.subquery()))
    rows = ctx.session.scalars(q.order_by(m.ResourceSnapshot.resource_id).limit(page.limit).offset(page.offset))
    return paged([resource_view(r) for r in rows], total, page)


@router.get("/request-fixtures")
def list_request_fixtures(ctx: RequestContext = Depends(get_ctx)):
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    out = []
    for s in ctx.session.scalars(select(m.RequestFixtureSet).order_by(m.RequestFixtureSet.created_at.desc())):
        items = [i for i in s.items if readable is None
                 or (i.get("scope_id") or i.get("account_scope_id")) in readable]
        out.append({"id": s.id, "provider": s.provider, "label": s.label, "provenance": s.provenance,
                    "content_digest": s.content_digest, "items": items})
    return {"items": out, "total": len(out)}


@router.get("/bindings")
def list_bindings(provider: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    q = select(m.PolicyBinding).order_by(m.PolicyBinding.id)
    if provider:
        q = q.where(m.PolicyBinding.provider == provider)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    rows = [b for b in ctx.session.scalars(q) if readable is None or b.target_scope_id in readable]
    return {"items": [binding_view(b) for b in rows], "total": len(rows)}


@router.get("/readiness-evidence")
def list_evidence(control_id: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    q = select(m.ReadinessEvidence).order_by(m.ReadinessEvidence.collected_at.desc())
    if control_id:
        q = q.where(m.ReadinessEvidence.control_id == control_id)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    rows = [e for e in ctx.session.scalars(q) if readable is None or e.scope_id in readable]
    return {"items": [evidence_view(e) for e in rows], "total": len(rows)}


@router.post("/readiness-evidence", status_code=201)
def create_evidence(payload: ReadinessEvidenceCreate, ctx: RequestContext = Depends(get_ctx)):
    scope = ctx.tree.get(payload.scope_id)
    if not (ctx.principal.has_role_at(Role.CONTROL_ENGINEER, scope.id, ctx.tree)
            or ctx.principal.has_role_at(Role.CLOUD_ENGINEER, scope.id, ctx.tree)
            or ctx.principal.has_role_at(Role.EXCEPTION_REQUESTER, scope.id, ctx.tree)):
        raise Forbidden("Recording readiness evidence requires an engineering or application role at this scope.")
    control = ctx.session.get(m.Control, payload.control_id)
    if control is None:
        raise NotFound("Control not found")
    prereqs = {p["id"] for p in get_provider(scope.provider).readiness_prerequisites()}
    if payload.prerequisite not in prereqs:
        raise ValidationFailed(f"Unknown prerequisite for {scope.provider}: {sorted(prereqs)}")
    collected = payload.collected_at or ctx.now
    if collected > ctx.now:
        raise ValidationFailed("collected_at cannot be in the future")
    e = m.ReadinessEvidence(id=new_id("evid"), control_id=control.id, application=payload.application,
                            scope_id=scope.id, prerequisite=payload.prerequisite, status=payload.status,
                            summary=payload.summary, evidence_ref=payload.evidence_ref,
                            provided_by=f"{ctx.principal.display_name} ({ctx.principal.team})", provenance="MANUAL",
                            collected_at=collected, created_at=ctx.now, created_by=ctx.principal.user_id)
    ctx.session.add(e)
    ctx.session.flush()
    audit.record(ctx, action="readiness_evidence.recorded", object_type="readiness_evidence", object_id=e.id,
                 scope_ids=[scope.id], control_id=control.id,
                 details={"prerequisite": e.prerequisite, "status": e.status, "application": e.application})
    ctx.commit()
    return evidence_view(e)


@router.get("/work-items")
def list_work(status: str | None = None, kind: str | None = None, page: Page = Depends(),
              ctx: RequestContext = Depends(get_ctx)):
    q = select(m.WorkReference).order_by(m.WorkReference.created_at.desc(), m.WorkReference.id)
    if status:
        q = q.where(m.WorkReference.status == status)
    if kind:
        q = q.where(m.WorkReference.kind == kind)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    rows = [w for w in ctx.session.scalars(q) if readable is None or w.scope_id in readable]
    return paged([work_view(w) for w in rows[page.offset:page.offset + page.limit]], len(rows), page)


@router.post("/work-items", status_code=201)
def create_work(payload: WorkReferenceCreate, ctx: RequestContext = Depends(get_ctx)):
    require_any_role(ctx.principal, [Role.CONTROL_ENGINEER, Role.CLOUD_ENGINEER, Role.SECURITY_APPROVER],
                     action="Linking external work")
    w = m.WorkReference(id=new_id("work"), status="OPEN", created_at=ctx.now, created_by=ctx.principal.user_id,
                        dedupe_key=None, **payload.model_dump())
    ctx.session.add(w)
    ctx.session.flush()
    audit.record(ctx, action="work_reference.linked", object_type="work_reference", object_id=w.id,
                 scope_ids=[w.scope_id] if w.scope_id else [], details={"kind": w.kind, "owner": w.owner_team})
    ctx.commit()
    return work_view(w)


@router.post("/work-items/{work_id}/close")
def close_work(work_id: str, ctx: RequestContext = Depends(get_ctx)):
    w = ctx.session.get(m.WorkReference, work_id)
    if w is None or (w.scope_id is not None and not ctx.principal.can_read_scope(w.scope_id, ctx.tree)):
        raise NotFound("Work item not found")
    require_any_role(ctx.principal, [Role.CONTROL_ENGINEER, Role.CLOUD_ENGINEER, Role.SECURITY_APPROVER],
                     action="Closing a work reference")
    w.status = "CLOSED"
    w.closed_at = ctx.now
    audit.record(ctx, action="work_reference.closed", object_type="work_reference", object_id=w.id,
                 scope_ids=[w.scope_id] if w.scope_id else [])
    ctx.commit()
    return work_view(w)


# ------------------------------------------------------------------------------------ assessments


@router.post("/assessments", status_code=201)
def run_assessment(payload: AssessmentCreate, ctx: RequestContext = Depends(get_ctx)):
    run = assessments.run_assessment(ctx, payload)
    ctx.commit()
    return run_view(run)


@router.get("/assessments")
def list_assessments(control_id: str | None = None, page: Page = Depends(), ctx: RequestContext = Depends(get_ctx)):
    q = select(m.AssessmentRun).order_by(m.AssessmentRun.seq.desc())
    if control_id:
        q = q.where(m.AssessmentRun.control_id == control_id)
    total = ctx.session.scalar(select(func.count()).select_from(q.subquery()))
    rows = ctx.session.scalars(q.limit(page.limit).offset(page.offset))
    return paged([{k: v for k, v in assessments.filtered_run_view(ctx, r).items() if k != "inputs"} for r in rows],
                 total, page)


@router.get("/assessments/{run_id}")
def get_assessment(run_id: str, ctx: RequestContext = Depends(get_ctx)):
    return assessments.filtered_run_view(ctx, assessments.get_run(ctx, run_id))


@router.get("/assessments/{run_id}/results")
def get_assessment_results(run_id: str, page: Page = Depends(),
                           subject_kind: str | None = Query(None, pattern="^(RESOURCE|REQUEST)$"),
                           configuration_result: str | None = None, exception_disposition: str | None = None,
                           request_impact: str | None = None, readiness: str | None = None,
                           applicability: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    rows, total = assessments.list_results(
        ctx, run_id, subject_kind=subject_kind,
        filters={"configuration_result": configuration_result, "exception_disposition": exception_disposition,
                 "request_impact": request_impact, "readiness": readiness, "applicability": applicability},
        limit=page.limit, offset=page.offset)
    return paged([result_view(r) for r in rows], total, page)


@router.post("/assessments/{run_id}/route-blockers")
def route_blockers(run_id: str, ctx: RequestContext = Depends(get_ctx)):
    out = assessments.route_blockers(ctx, run_id)
    ctx.commit()
    return {"created": out}


@router.get("/simulation")
def simulation_disclosure(ctx: RequestContext = Depends(get_ctx)):
    """Compatibility route. The feature is Impact Assessment; this states exactly what is evaluated."""
    return {
        "feature": "Impact Assessment",
        "redirect": "/assessments",
        "what_is_evaluated": [
            "Configuration of resources in a versioned inventory snapshot (fixture) against a versioned evaluator.",
            "Supplied representative create/update request fixtures against the supported deny model.",
            "Supplied readiness evidence and its freshness.",
        ],
        "what_is_not_evaluated": [
            "Arbitrary IAM or Azure Policy documents (no general interpreter).",
            "End-to-end authorization (identity, resource, boundary and session policies).",
            "Application availability or network reachability (no network tests).",
            "Future requests that were not supplied as fixtures.",
        ],
        "infrastructure_changes": "None. Assessments never modify infrastructure.",
    }
