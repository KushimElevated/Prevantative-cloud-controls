"""Control Detail aggregate and the next required decision."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.clock import iso
from app.models import entities as m
from app.models.enums import AssessmentStatus, PackageStatus, PlanState, RevisionStatus, ValidationOutcome
from app.providers.registry import get_provider
from app.services import settings
from app.services.context import RequestContext
from app.services.controls import get_control
from app.services.dashboard import resource_coverage
from app.services.exceptions import decisions_for, effective_status, exception_view
from app.services.implementations import latest_validation, revision_digest
from app.services.rollouts import evaluate_gates, failing, latest_package, package_decisions
from app.services.views import (
    binding_view,
    bundle_view,
    control_revision_view,
    decision_view,
    implementation_revision_view,
    package_view,
    plan_view,
    run_view,
    target_view,
    validation_view,
)


def next_decisions(ctx: RequestContext, control: m.Control, impl_infos: list[dict], plans: list[m.RolloutPlan],
                   runs: list[m.AssessmentRun], exceptions: list[m.SecurityException]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []

    def add(decision: str, role: str, detail: str) -> None:
        out.append({"decision": decision, "owner_role": role, "detail": detail})

    latest = control.revisions[-1]
    if latest.status == RevisionStatus.DRAFT:
        add("Submit control revision for review", "CONTROL_ENGINEER",
            f"Revision {latest.revision} is a draft; submitted revisions become immutable.")
    if not impl_infos:
        add("Propose an implementation", "CONTROL_ENGINEER", "No provider implementation exists yet.")
    for info in impl_infos:
        rev, val = info["_rev"], info["_validation"]
        if val is None:
            add(f"Validate implementation {info['implementation']['name']}", "CONTROL_ENGINEER",
                "No validation for the current content.")
        elif val.outcome != ValidationOutcome.PASS:
            add(f"Resolve implementation feasibility ({val.outcome})", "CONTROL_ENGINEER",
                "; ".join(c["message"] for c in val.checks if c["status"] != "PASS")[:300])
        elif rev.status == RevisionStatus.DRAFT:
            add(f"Submit implementation revision {rev.revision}", "CONTROL_ENGINEER", "Validated draft.")
        elif not val.integration_ready:
            add("Integration readiness blocked", "CONTROL_ENGINEER",
                "; ".join(val.integration_blockers))
    run = runs[0] if runs else None
    if run is None:
        add("Run an impact assessment", "CONTROL_ENGINEER", "No assessment recorded.")
    elif run.status == AssessmentStatus.UNSUPPORTED:
        add("Assessment unsupported", "CONTROL_ENGINEER", "Implementation has no supported evaluator.")
    pending = [e for e in exceptions if effective_status(e, ctx.now) in ("REQUESTED", "SECURITY_REVIEW")]
    if pending:
        add(f"Decide {len(pending)} pending exception request(s)", "SECURITY_APPROVER",
            ", ".join(e.id for e in pending))
    active = [p for p in plans if p.state != PlanState.CANCELLED]
    if run is not None and run.status == AssessmentStatus.COMPLETED and not active:
        add("Create a rollout plan", "CONTROL_ENGINEER", "Assessment exists; no active plan.")
    for plan in active:
        pkg = latest_package(ctx, plan.id)
        if plan.state == PlanState.PAUSED:
            add("Resume or cancel paused rollout", "CLOUD_ENGINEER", plan.state_reason or "")
        if pkg is None or pkg.status in (PackageStatus.SUPERSEDED, PackageStatus.STALE, PackageStatus.REJECTED):
            add("Submit a change package", "CONTROL_ENGINEER",
                f"Latest package status: {pkg.status if pkg else 'none'}")
            continue
        if pkg.status == PackageStatus.IN_REVIEW:
            pre = failing(evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=False))
            if pre:
                add("Resolve failing package gates", "CONTROL_ENGINEER", ", ".join(g["id"] for g in pre))
            else:
                roles = {d.role for d in package_decisions(ctx, pkg)
                         if d.subject_digest == pkg.manifest_digest and d.decision == "APPROVE"}
                if "SECURITY_APPROVER" not in roles:
                    add("Security approval of the change package", "SECURITY_APPROVER", pkg.manifest_digest)
                if "CLOUD_ENGINEER" not in roles:
                    add("Cloud Engineering readiness approval", "CLOUD_ENGINEER", pkg.manifest_digest)
            continue
        bundle = ctx.session.scalars(select(m.HandoffBundle).where(
            m.HandoffBundle.package_id == pkg.id, m.HandoffBundle.kind == "APPROVED_HANDOFF")).first()
        if bundle is None:
            add("Export the approved handoff bundle", "CLOUD_ENGINEER", f"Package r{pkg.package_revision} approved.")
            continue
        stages = [r["stage"] for r in pkg.manifest["deploy_rings"]]
        targets = list(ctx.session.scalars(select(m.DeliveryTarget).where(m.DeliveryTarget.bundle_id == bundle.id)))
        current = [t for t in targets if t.ring_stage == plan.stage]
        if any(t.state == "DRIFTED" for t in targets):
            add("Investigate drift", "CLOUD_ENGINEER", "Observed state differs from approved artifacts.")
        if current and any(t.state != "VERIFIED" for t in current):
            add(f"Deliver and verify {plan.stage} ring", "CLOUD_ENGINEER",
                ", ".join(f"{t.native_identity.rsplit('/', 1)[-1]}={t.state}" for t in current))
            continue
        later = [s for s in stages if stages.index(s) > (stages.index(plan.stage) if plan.stage in stages else -1)]
        if later:
            add(f"Advance rollout to {later[0]}", "CLOUD_ENGINEER", "Previous ring evidence is in place.")
        else:
            add("Plan next package (later rings)", "CONTROL_ENGINEER",
                "All rings in the approved package are delivered.")
    if not out:
        add("No decision pending", "-", "Monitor coverage, expiry and drift.")
    return out


def control_detail(ctx: RequestContext, control_id: str) -> dict[str, Any]:
    control = get_control(ctx, control_id)
    tree = ctx.tree
    readable = ctx.principal.readable_scope_ids(tree)
    impl_infos = []
    for impl in control.implementations:
        rev = impl.revisions[-1]
        val = latest_validation(ctx, rev)
        provider = get_provider(impl.provider)
        impl_infos.append({
            "implementation": {"id": impl.id, "name": impl.name, "provider": impl.provider,
                               "mechanism_role": impl.mechanism_role, "management": impl.management},
            "revisions": [implementation_revision_view(r) for r in impl.revisions],
            "latest_revision_digest": revision_digest(rev),
            "latest_validation": validation_view(val) if val else None,
            "capabilities": provider.capabilities().to_dict(),
            "prerequisites": provider.readiness_prerequisites(),
            "_rev": rev, "_validation": val,
        })
    bindings = [b for b in ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider.in_(
        control.revisions[-1].providers)).order_by(m.PolicyBinding.id))
        if (b.control_id in (None, control.id)) and (readable is None or b.target_scope_id in readable)]
    runs = list(ctx.session.scalars(select(m.AssessmentRun).where(m.AssessmentRun.control_id == control.id)
                                    .order_by(m.AssessmentRun.seq.desc())))
    exceptions = [e for e in ctx.session.scalars(select(m.SecurityException).where(
        m.SecurityException.control_id == control.id).order_by(m.SecurityException.created_at))
        if readable is None or e.scope_id in readable]
    plans = list(ctx.session.scalars(select(m.RolloutPlan).where(m.RolloutPlan.control_id == control.id)
                                     .order_by(m.RolloutPlan.created_at)))
    if readable is not None:
        plans = [p for p in plans if p.target_scope_id in readable]
    warn = settings.get(ctx.session, "expiry")["warning_days"]
    plan_views = []
    for plan in plans:
        pkgs = list(ctx.session.scalars(select(m.ChangePackage).where(m.ChangePackage.plan_id == plan.id)
                                        .order_by(m.ChangePackage.package_revision.desc())))
        pkg_views = []
        for p in pkgs:
            bundles = list(ctx.session.scalars(select(m.HandoffBundle).where(m.HandoffBundle.package_id == p.id)
                                               .order_by(m.HandoffBundle.exported_at)))
            targets = []
            for b in bundles:
                targets += [target_view(t) for t in ctx.session.scalars(select(m.DeliveryTarget).where(
                    m.DeliveryTarget.bundle_id == b.id).order_by(m.DeliveryTarget.ring_stage,
                                                                 m.DeliveryTarget.native_identity))]
            pkg_views.append({**package_view(p, include_manifest=False),
                              "decisions": [decision_view(d) for d in package_decisions(ctx, p)],
                              "bundles": [bundle_view(b) for b in bundles], "delivery_targets": targets})
        plan_views.append({**plan_view(plan), "packages": pkg_views})
    latest_completed = next((r for r in runs if r.status == AssessmentStatus.COMPLETED), None)
    decisions = next_decisions(ctx, control, impl_infos, plans, runs, exceptions)
    for info in impl_infos:
        info.pop("_rev")
        info.pop("_validation")
    return {
        "control": {"id": control.id, "origin": control.origin, "operational_owner": control.operational_owner,
                    "created_at": iso(control.created_at), "created_by": control.created_by},
        "revisions": [control_revision_view(r) for r in control.revisions],
        "current_revision": control_revision_view(control.revisions[-1]),
        "implementations": impl_infos,
        "existing_bindings": [binding_view(b) for b in bindings],
        "assessments": [{k: v for k, v in run_view(r).items() if k not in ("inputs",)} for r in runs[:10]],
        "coverage": resource_coverage(ctx, control.id, latest_completed) if latest_completed else None,
        "exceptions": [exception_view(e, ctx.now, warn, decisions_for(ctx, e.id)) for e in exceptions],
        "rollout_plans": plan_views,
        "next_decision": decisions[0],
        "pending_decisions": decisions,
    }
