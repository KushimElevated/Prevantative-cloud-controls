from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.api.deps import get_ctx
from app.api.schemas import (
    AdvanceCommand,
    BindingObservation,
    MockObservation,
    MockPipelineRun,
    PackageCreate,
    PackageDecision,
    PlanCreate,
    PlanStateCommand,
    PlanUpdate,
    ReceiptPayload,
)
from app.models import entities as m
from app.providers.handoff import deterministic_zip
from app.services import handoffs, rollouts
from app.services.context import RequestContext
from app.services.views import (
    bundle_view,
    decision_view,
    observation_view,
    package_view,
    plan_view,
    receipt_view,
    target_view,
)

router = APIRouter(tags=["rollouts", "handoffs"])


def _plan_full(ctx: RequestContext, plan: m.RolloutPlan) -> dict:
    pkgs = list(ctx.session.scalars(select(m.ChangePackage).where(m.ChangePackage.plan_id == plan.id)
                                    .order_by(m.ChangePackage.package_revision.desc())))
    return {**plan_view(plan), "packages": [package_view(p, include_manifest=False) for p in pkgs]}


@router.get("/rollout-plans")
def list_plans(control_id: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    q = select(m.RolloutPlan).order_by(m.RolloutPlan.created_at.desc())
    if control_id:
        q = q.where(m.RolloutPlan.control_id == control_id)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    rows = [p for p in ctx.session.scalars(q) if readable is None or p.target_scope_id in readable]
    return {"items": [_plan_full(ctx, p) for p in rows], "total": len(rows)}


@router.get("/rollout-plans/template")
def plan_template(control_id: str, implementation_revision_id: str, ctx: RequestContext = Depends(get_ctx)):
    return rollouts.plan_template(ctx, control_id, implementation_revision_id)


@router.post("/rollout-plans", status_code=201)
def create_plan(payload: PlanCreate, ctx: RequestContext = Depends(get_ctx)):
    plan = rollouts.create_plan(ctx, payload)
    ctx.commit()
    return _plan_full(ctx, plan)


@router.get("/rollout-plans/{plan_id}")
def get_plan(plan_id: str, ctx: RequestContext = Depends(get_ctx)):
    return _plan_full(ctx, rollouts.get_plan(ctx, plan_id))


@router.put("/rollout-plans/{plan_id}")
def update_plan(plan_id: str, payload: PlanUpdate, ctx: RequestContext = Depends(get_ctx)):
    plan = rollouts.update_plan(ctx, plan_id, payload)
    ctx.commit()
    return _plan_full(ctx, plan)


@router.post("/rollout-plans/{plan_id}/packages", status_code=201)
def create_package(plan_id: str, payload: PackageCreate, ctx: RequestContext = Depends(get_ctx)):
    pkg = rollouts.create_package(ctx, plan_id, payload.through_stage)
    ctx.commit()
    return package_view(pkg)


@router.post("/rollout-plans/{plan_id}/advance")
def advance(plan_id: str, payload: AdvanceCommand, ctx: RequestContext = Depends(get_ctx)):
    plan = rollouts.advance(ctx, plan_id, payload)
    ctx.commit()
    return _plan_full(ctx, plan)


def _set_state(ctx: RequestContext, plan_id: str, action: str, payload: PlanStateCommand) -> dict:
    plan = rollouts.set_state(ctx, plan_id, action, payload.reason, payload.expected_lock_version)
    ctx.commit()
    return _plan_full(ctx, plan)


@router.post("/rollout-plans/{plan_id}/pause")
def pause(plan_id: str, payload: PlanStateCommand, ctx: RequestContext = Depends(get_ctx)):
    return _set_state(ctx, plan_id, "pause", payload)


@router.post("/rollout-plans/{plan_id}/resume")
def resume(plan_id: str, payload: PlanStateCommand, ctx: RequestContext = Depends(get_ctx)):
    return _set_state(ctx, plan_id, "resume", payload)


@router.post("/rollout-plans/{plan_id}/cancel")
def cancel(plan_id: str, payload: PlanStateCommand, ctx: RequestContext = Depends(get_ctx)):
    return _set_state(ctx, plan_id, "cancel", payload)


@router.get("/change-packages/{package_id}")
def get_package(package_id: str, ctx: RequestContext = Depends(get_ctx)):
    pkg = rollouts.get_package(ctx, package_id)
    bundles = list(ctx.session.scalars(select(m.HandoffBundle).where(m.HandoffBundle.package_id == pkg.id)
                                       .order_by(m.HandoffBundle.exported_at)))
    targets = []
    for b in bundles:
        targets += [target_view(t) for t in ctx.session.scalars(select(m.DeliveryTarget).where(
            m.DeliveryTarget.bundle_id == b.id).order_by(m.DeliveryTarget.ring_stage, m.DeliveryTarget.native_identity))]
    return {**package_view(pkg), "decisions": [decision_view(d) for d in rollouts.package_decisions(ctx, pkg)],
            "bundles": [bundle_view(b) for b in bundles], "delivery_targets": targets}


@router.get("/change-packages/{package_id}/gates")
def get_gates(package_id: str, ctx: RequestContext = Depends(get_ctx)):
    gates = rollouts.live_gates(ctx, package_id)
    return {"gates": gates, "failing": rollouts.failing(gates), "evaluated_at": ctx.now.isoformat()}


@router.post("/change-packages/{package_id}/decisions")
def decide(package_id: str, payload: PackageDecision, ctx: RequestContext = Depends(get_ctx)):
    pkg = rollouts.decide_package(ctx, package_id, payload)
    ctx.commit()
    return package_view(pkg)


@router.post("/change-packages/{package_id}/export", status_code=201)
def export(package_id: str, ctx: RequestContext = Depends(get_ctx)):
    b = handoffs.export_bundle(ctx, package_id, approved=True)
    ctx.commit()
    return bundle_view(b)


@router.post("/change-packages/{package_id}/draft-export", status_code=201)
def draft_export(package_id: str, ctx: RequestContext = Depends(get_ctx)):
    b = handoffs.export_bundle(ctx, package_id, approved=False)
    ctx.commit()
    return bundle_view(b)


@router.get("/handoff-bundles/{bundle_id}")
def get_bundle(bundle_id: str, ctx: RequestContext = Depends(get_ctx)):
    b = handoffs.get_bundle(ctx, bundle_id)
    targets = [target_view(t) for t in ctx.session.scalars(select(m.DeliveryTarget).where(
        m.DeliveryTarget.bundle_id == b.id).order_by(m.DeliveryTarget.ring_stage, m.DeliveryTarget.native_identity))]
    receipts = [receipt_view(r) for r in ctx.session.scalars(select(m.DeploymentReceipt).where(
        m.DeploymentReceipt.bundle_id == b.id).order_by(m.DeploymentReceipt.seq))]
    observations = [observation_view(o) for o in ctx.session.scalars(select(m.Observation).where(
        m.Observation.delivery_target_id.in_([t["id"] for t in targets])).order_by(m.Observation.seq))]
    return {**bundle_view(b, include_files=True), "delivery_targets": targets, "receipts": receipts,
            "observations": observations}


@router.get("/handoff-bundles/{bundle_id}/download")
def download_bundle(bundle_id: str, ctx: RequestContext = Depends(get_ctx)):
    b = handoffs.get_bundle(ctx, bundle_id)
    data = deterministic_zip(b.files)
    name = f"ccp-{b.kind.lower()}-{b.bundle_digest.split(':')[1][:12]}.zip"
    return Response(content=data, media_type="application/zip",
                    headers={"content-disposition": f'attachment; filename="{name}"'})


@router.post("/receipts")
def ingest_receipt(payload: ReceiptPayload, ctx: RequestContext = Depends(get_ctx)):
    r, dup = handoffs.ingest_receipt(ctx, payload)
    ctx.commit()
    return {**receipt_view(r), "duplicate": dup}


@router.post("/mock-pipeline/runs")
def mock_pipeline(payload: MockPipelineRun, ctx: RequestContext = Depends(get_ctx)):
    out = handoffs.run_mock_pipeline(ctx, payload.bundle_id, payload.ring, payload.scenario)
    ctx.commit()
    return {"provenance": "MOCK", "receipts": out}


@router.post("/mock-observations", status_code=201)
def mock_observation(payload: MockObservation, ctx: RequestContext = Depends(get_ctx)):
    o = handoffs.record_mock_observation(ctx, payload.delivery_target_id, payload.scenario, payload.observed_at)
    ctx.commit()
    t = ctx.session.get(m.DeliveryTarget, payload.delivery_target_id)
    return {"observation": observation_view(o), "delivery_target": target_view(t)}


@router.post("/bindings/{binding_id}/mock-observation", status_code=201)
def binding_observation(binding_id: str, payload: BindingObservation, ctx: RequestContext = Depends(get_ctx)):
    o = handoffs.record_binding_observation(ctx, binding_id, payload.scenario)
    ctx.commit()
    return observation_view(o)


@router.get("/deliveries")
def list_deliveries(control_id: str | None = None, state: str | None = None, ctx: RequestContext = Depends(get_ctx)):
    q = select(m.DeliveryTarget).order_by(m.DeliveryTarget.state_changed_at.desc(), m.DeliveryTarget.id)
    if control_id:
        q = q.where(m.DeliveryTarget.control_id == control_id)
    if state:
        q = q.where(m.DeliveryTarget.state == state)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    rows = [t for t in ctx.session.scalars(q) if readable is None or t.scope_id in readable]
    return {"items": [target_view(t) for t in rows], "total": len(rows)}
