"""Rollout plans, change packages, gates and approvals.

Control lifecycle, rollout progression, native settings and delivery state are separate.
Approvals bind to the canonical digest of an immutable change manifest; attestations are
stored separately so hashing is never circular. Gates are evaluated in the backend in the
same transaction as the decision they protect. ADMIN has no bypass.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.auth.principal import require_any_role, require_global_role, require_role
from app.core.clock import iso
from app.core.digests import digest
from app.core.errors import Conflict, Forbidden, GateFailed, NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import (
    STAGE_ORDER,
    AssessmentStatus,
    DecisionValue,
    ExceptionStatus,
    PackageStatus,
    PlanState,
    Representability,
    RevisionStatus,
    Role,
    RolloutStage,
    ValidationOutcome,
)
from app.providers.registry import get_provider
from app.services import audit, settings
from app.services.assessments import latest_completed_run, latest_snapshot
from app.services.context import RequestContext
from app.services.controls import get_control, latest_revision
from app.services.exceptions import effective_status
from app.services.implementations import get_revision as get_impl_revision
from app.services.implementations import latest_validation, revision_digest

MANIFEST_SCHEMA = "ccp.change-package/v1"
APPROVAL_ROLES = [Role.SECURITY_APPROVER, Role.CLOUD_ENGINEER]


def stage_index(stage: str) -> int:
    return STAGE_ORDER.index(RolloutStage(stage))


# ------------------------------------------------------------------------------------ plans


def _validate_plan(ctx: RequestContext, control_id: str, payload) -> tuple[m.ImplementationRevision, list[dict]]:
    impl_rev = get_impl_revision(ctx, payload.implementation_revision_id)
    impl = impl_rev.implementation
    if impl.control_id != control_id:
        raise ValidationFailed("Implementation revision does not belong to this control.")
    provider = get_provider(impl.provider)
    target = ctx.tree.get(payload.target_scope_id)
    if target.provider != impl.provider:
        raise ValidationFailed("Target scope provider does not match implementation provider.")
    stages = [r.stage for r in payload.rings]
    if len(set(stages)) != len(stages) or stages != sorted(stages, key=stage_index):
        raise ValidationFailed("Rings must be unique and ordered OBSERVATION < PILOT < LIMITED < BROAD.")
    supported = provider.capabilities().supported_scope_types
    checks: list[dict] = []
    rings = []
    for ring in payload.rings:
        for sid in ring.scope_ids:
            scope = ctx.tree.get(sid)
            if scope.provider != impl.provider or scope.scope_type not in supported:
                raise ValidationFailed(f"Scope {sid} ({scope.scope_type}) is not a supported {impl.provider} scope.")
            if not ctx.tree.is_within(sid, target.id):
                raise ValidationFailed(f"Ring scope {sid} is outside the plan target {target.id}.")
        ring_checks = provider.validate_ring_settings(ring.stage, ring.settings, impl_rev)
        checks.extend(c.to_dict() for c in ring_checks)
        bad = [c for c in ring_checks if c.status in ("FAIL", "UNSUPPORTED", "REQUIRES_DIFFERENT_MECHANISM")]
        if bad:
            code = "UNSUPPORTED" if any(c.status == "UNSUPPORTED" for c in bad) else "VALIDATION_FAILED"
            raise ValidationFailed(f"Ring {ring.stage} settings are not valid for {impl.provider}: "
                                   + "; ".join(c.message for c in bad), code=code,
                                   details=[c.to_dict() for c in bad])
        existing = ring.settings.get("use_existing_binding_id")
        if existing:
            b = ctx.session.get(m.PolicyBinding, existing)
            if b is None or b.provider != impl.provider or b.definition_ref.lower() != impl_rev.source_ref.lower():
                raise ValidationFailed("use_existing_binding_id must reference an existing binding of the same "
                                       "policy definition.")
            if not all(ctx.tree.is_within(sid, b.target_scope_id) for sid in ring.scope_ids):
                raise ValidationFailed("Existing binding does not cover the ring scopes.")
        rings.append({"stage": ring.stage, "scope_ids": sorted(ring.scope_ids), "settings": ring.settings})
    return impl_rev, rings


def create_plan(ctx: RequestContext, payload) -> m.RolloutPlan:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Creating a rollout plan")
    control = get_control(ctx, payload.control_id)
    impl_rev, rings = _validate_plan(ctx, control.id, payload)
    active = ctx.session.scalars(select(m.RolloutPlan).where(
        m.RolloutPlan.control_id == control.id, m.RolloutPlan.provider == impl_rev.implementation.provider,
        m.RolloutPlan.state != PlanState.CANCELLED)).first()
    if active:
        raise Conflict(f"Plan {active.id} is already active for this control and provider; edit or cancel it.")
    plan = m.RolloutPlan(
        id=new_id("plan"), control_id=control.id, title=payload.title, provider=impl_rev.implementation.provider,
        target_scope_id=payload.target_scope_id, implementation_revision_ids=[impl_rev.id], rings=rings,
        pause_criteria=payload.pause_criteria, rollback_plan=payload.rollback_plan.model_dump(),
        prerequisite_changes=payload.prerequisite_changes, deployment_instructions=payload.deployment_instructions,
        stage=RolloutStage.ASSESSMENT, state=PlanState.ACTIVE, created_by=ctx.principal.user_id,
        created_at=ctx.now, updated_at=ctx.now)
    ctx.session.add(plan)
    ctx.session.flush()
    audit.record(ctx, action="rollout_plan.created", object_type="rollout_plan", object_id=plan.id,
                 control_id=control.id, scope_ids=[plan.target_scope_id], after_digest=digest(plan_content(plan)))
    return plan


def plan_content(plan: m.RolloutPlan) -> dict[str, Any]:
    return {"title": plan.title, "target_scope_id": plan.target_scope_id,
            "implementation_revision_ids": plan.implementation_revision_ids, "rings": plan.rings,
            "pause_criteria": plan.pause_criteria, "rollback_plan": plan.rollback_plan,
            "prerequisite_changes": plan.prerequisite_changes,
            "deployment_instructions": plan.deployment_instructions}


def get_plan(ctx: RequestContext, plan_id: str, lock: bool = False) -> m.RolloutPlan:
    plan = ctx.session.get(m.RolloutPlan, plan_id, with_for_update=lock)
    if plan is None:
        raise NotFound(f"Rollout plan {plan_id} not found")
    if not ctx.principal.can_read_scope(plan.target_scope_id, ctx.tree):
        raise NotFound(f"Rollout plan {plan_id} not found")
    return plan


def update_plan(ctx: RequestContext, plan_id: str, payload) -> m.RolloutPlan:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Editing a rollout plan")
    plan = get_plan(ctx, plan_id, lock=True)
    if plan.state == PlanState.CANCELLED:
        raise Conflict("Cancelled plans cannot be edited.")
    if plan.lock_version != payload.expected_lock_version:
        raise Conflict("Plan changed since you loaded it.", code="STALE_VERSION")
    impl_rev, rings = _validate_plan(ctx, plan.control_id, payload)
    before = digest(plan_content(plan))
    plan.title = payload.title
    plan.target_scope_id = payload.target_scope_id
    plan.implementation_revision_ids = [impl_rev.id]
    plan.rings = rings
    plan.pause_criteria = payload.pause_criteria
    plan.rollback_plan = payload.rollback_plan.model_dump()
    plan.prerequisite_changes = payload.prerequisite_changes
    plan.deployment_instructions = payload.deployment_instructions
    plan.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="rollout_plan.updated", object_type="rollout_plan", object_id=plan.id,
                 control_id=plan.control_id, before_digest=before, after_digest=digest(plan_content(plan)),
                 reason="Material plan change; existing packages must be resubmitted and reapproved.")
    return plan


# ------------------------------------------------------------------------------------ manifest


def _deploy_rings(plan: m.RolloutPlan, through_stage: str) -> list[dict]:
    return [r for r in plan.rings if stage_index(r["stage"]) <= stage_index(through_stage)]


def delivered_marker(plan_id: str) -> str:
    return f"ccp-plan:{plan_id}"


def _baseline(ctx: RequestContext, plan: m.RolloutPlan) -> list[dict]:
    tree = ctx.tree
    target_family = set(tree.descendants(plan.target_scope_id)) | set(tree.ancestors(plan.target_scope_id))
    out = []
    for b in ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider == plan.provider)
                                 .order_by(m.PolicyBinding.native_id)):
        if b.source_repository == delivered_marker(plan.id):
            continue  # bindings delivered by this plan are tracked as delivery state, not baseline
        if b.target_scope_id in target_family:
            out.append({"id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                        "management": b.management, "origin": b.origin, "settings": b.settings,
                        "desired_digest": digest(b.desired_state),
                        "observed_digest": digest(b.observed_state) if b.observed_state is not None else None})
    return out


def _approved_by(ctx: RequestContext, exc: m.SecurityException) -> str | None:
    d = ctx.session.scalars(select(m.ApprovalDecision).where(
        m.ApprovalDecision.subject_type == "EXCEPTION", m.ApprovalDecision.subject_id == exc.id,
        m.ApprovalDecision.decision == DecisionValue.APPROVE).order_by(m.ApprovalDecision.decided_at.desc())).first()
    return d.actor_id if d else None


def build_manifest(ctx: RequestContext, plan: m.RolloutPlan, through_stage: str) -> dict[str, Any]:
    tree = ctx.tree
    provider = get_provider(plan.provider)
    control = get_control(ctx, plan.control_id)
    crev = latest_revision(ctx, control.id, non_draft=True) or latest_revision(ctx, control.id)
    impl_rev = get_impl_revision(ctx, plan.implementation_revision_ids[0])
    impl = impl_rev.implementation
    validation = latest_validation(ctx, impl_rev)
    deploy = _deploy_rings(plan, through_stage)
    rings_out = []
    for ring in deploy:
        rings_out.append({
            "stage": ring["stage"], "scope_ids": ring["scope_ids"],
            "scope_native_ids": [tree.get(s).native_id for s in ring["scope_ids"]],
            "settings": ring["settings"],
            "enforcing": provider.ring_is_enforcing(ring["settings"]),
            "artifact_required": provider.ring_needs_artifact(ring["settings"]),
            "resolved_descendants": {s: tree.descendants(s) for s in ring["scope_ids"]},
        })
    enforcing_scopes = [s for r in rings_out if r["enforcing"] for s in r["scope_ids"]]
    additions, removals = [], []
    for exc in ctx.session.scalars(select(m.SecurityException).where(
            m.SecurityException.control_id == control.id).order_by(m.SecurityException.id)):
        eff = effective_status(exc, ctx.now)
        in_enforcing = any(tree.is_within(exc.scope_id, s) for s in enforcing_scopes)
        in_target = tree.is_within(exc.scope_id, plan.target_scope_id)
        if (eff == ExceptionStatus.APPROVED and exc.representability == Representability.REPRESENTABLE
                and exc.implementation_id == impl.id and in_enforcing):
            # Included regardless of native state so that delivering the exemption does not itself change the
            # manifest. Re-applying an identical exemption is idempotent for Cloud Engineering.
            additions.append({
                "exception_id": exc.id, "lineage_id": exc.lineage_id, "revision": exc.revision,
                "renews_exception_id": exc.renews_exception_id, "scope_id": exc.scope_id,
                "granularity": exc.granularity, "resource_ids": sorted(exc.resource_ids or []),
                "principal_patterns": sorted(exc.principal_patterns or []), "application": exc.application,
                "requester": exc.requester_id, "approved_by": _approved_by(ctx, exc), "risk_owner": exc.risk_owner,
                "business_justification": exc.business_justification, "valid_from": iso(exc.valid_from),
                "expires_at": iso(exc.expires_at), "compensating_controls": exc.compensating_controls,
                "native_representation": exc.native_representation,
            })
        elif (eff in (ExceptionStatus.EXPIRED, ExceptionStatus.REVOKED) and in_target
              and exc.native_status in ("APPLIED", "REMOVAL_PENDING")):
            removals.append({"exception_id": exc.id, "scope_id": exc.scope_id, "effective_status": eff,
                             "native_status": exc.native_status,
                             "native_expiry_supported": exc.native_expiry_supported,
                             "action": "Delete expired exemption object (no longer honored)"
                             if exc.native_expiry_supported else
                             "Remove exemption representation (no native expiry; still in force)"})
    consequences = []
    for a in additions:
        if plan.provider == "azure":
            text = (f"At {a['expires_at']} Azure stops honoring this exemption (the object remains). Afterwards, "
                    f"create/update requests for {a['resource_ids'] or a['scope_id']} that keep public network "
                    "access enabled will be denied unless remediated or a renewal is approved and applied.")
        else:
            text = (f"No native expiry. At {a['expires_at']} Cloud Engineering must remove the representation "
                    "through a reviewed change; until then the exception remains in force natively.")
        consequences.append({"exception_id": a["exception_id"], "expires_at": a["expires_at"], "consequence": text})
    baseline = _baseline(ctx, plan)
    run = latest_completed_run(ctx, crev.id, impl_rev.id, plan.target_scope_id)
    return {
        "schema": MANIFEST_SCHEMA,
        "plan": {"id": plan.id, "title": plan.title, "target_scope_id": plan.target_scope_id,
                 "target_scope_native_id": tree.get(plan.target_scope_id).native_id,
                 "through_stage": through_stage, "all_rings": plan.rings, "plan_digest": digest(plan_content(plan))},
        "control": {"id": control.id, "revision": crev.revision, "revision_id": crev.id,
                    "content_digest": crev.content_digest, "name": crev.name,
                    "security_objective": crev.security_objective, "prevention_boundary": crev.prevention_boundary,
                    "limitations": crev.limitations, "severity": crev.severity,
                    "security_owner": crev.security_owner, "engineering_owner": crev.engineering_owner},
        "implementations": [{
            "id": impl.id, "name": impl.name, "revision_id": impl_rev.id, "revision": impl_rev.revision,
            "provider": impl.provider, "policy_kind": impl_rev.policy_kind, "source_kind": impl_rev.source_kind,
            "source_ref": impl_rev.source_ref, "pinned_version": impl_rev.pinned_version,
            "content_digest": impl_rev.content_digest, "revision_digest": revision_digest(impl_rev),
            "parameters": impl_rev.parameters, "assignment_settings": impl_rev.assignment_settings,
            "verification_status": impl_rev.verification.get("status"),
            "validation_id": validation.id if validation else None,
            "evaluator": ({"id": validation.evaluator_id, "version": validation.evaluator_version}
                          if validation and validation.evaluator_id else None),
            "integration_ready": bool(validation and validation.integration_ready),
        }],
        "deploy_rings": rings_out,
        "prerequisite_changes": plan.prerequisite_changes,
        "exception_set": {"additions": additions,
                          "renewals": [a["exception_id"] for a in additions if a["renews_exception_id"]],
                          "removals": removals, "expiry_consequences": consequences},
        "baseline": {"bindings": baseline, "digest": digest(baseline)},
        "assessment_refs": ([{
            "run_id": run.id, "status": run.status, "evaluator_id": run.evaluator_id,
            "evaluator_version": run.evaluator_version, "input_digest": run.input_digest,
            "result_digest": run.result_digest, "assessed_at": iso(run.assessed_at),
            "inventory_snapshot_id": run.inventory_snapshot_id,
            "request_fixture_set_id": run.request_fixture_set_id}] if run else []),
        "rollback_plan": plan.rollback_plan,
        "pause_criteria": plan.pause_criteria,
        "deployment_instructions": plan.deployment_instructions,
        "provider_capabilities": {
            "native_audit_mode": provider.capabilities().native_audit_mode,
            "native_expiry_supported": provider.capabilities().native_expiry_supported,
            "integration_status": provider.capabilities().integration_status,
        },
    }


# ------------------------------------------------------------------------------------ gates


def _gate(gates: list, gid: str, label: str, ok: bool, detail: Any, phase: str = "PRE_APPROVAL") -> None:
    gates.append({"id": gid, "label": label, "status": "PASS" if ok else "FAIL", "detail": detail, "phase": phase})


def package_decisions(ctx: RequestContext, package: m.ChangePackage) -> list[m.ApprovalDecision]:
    return list(ctx.session.scalars(select(m.ApprovalDecision).where(
        m.ApprovalDecision.subject_type == "CHANGE_PACKAGE", m.ApprovalDecision.subject_id == package.id)
        .order_by(m.ApprovalDecision.decided_at, m.ApprovalDecision.id)))


def package_authors(ctx: RequestContext, package: m.ChangePackage, plan: m.RolloutPlan) -> set[str]:
    man = package.manifest
    crev = ctx.session.get(m.ControlRevision, man["control"]["revision_id"])
    authors = {package.created_by, plan.created_by}
    if crev:
        authors |= {crev.created_by, crev.submitted_by}
    for i in man["implementations"]:
        irev = ctx.session.get(m.ImplementationRevision, i["revision_id"])
        if irev:
            authors |= {irev.created_by, irev.submitted_by}
    return {a for a in authors if a}


def evaluate_gates(ctx: RequestContext, plan: m.RolloutPlan, manifest: dict[str, Any],
                   package: m.ChangePackage | None = None, include_approvals: bool = False) -> list[dict]:
    tree = ctx.tree
    gates: list[dict] = []
    fresh = settings.get(ctx.session, "evidence_freshness")
    thresholds = settings.get(ctx.session, "rollout")
    provider = get_provider(plan.provider)

    crev = ctx.session.get(m.ControlRevision, manifest["control"]["revision_id"])
    latest_nd = latest_revision(ctx, plan.control_id, non_draft=True)
    _gate(gates, "control_revision", "Control revision submitted and current",
          bool(crev and crev.status in (RevisionStatus.IN_REVIEW, RevisionStatus.APPROVED) and latest_nd
               and latest_nd.id == crev.id),
          f"Revision {manifest['control']['revision']} status {crev.status if crev else 'missing'}")

    impl_ref = manifest["implementations"][0]
    irev = ctx.session.get(m.ImplementationRevision, impl_ref["revision_id"])
    v = latest_validation(ctx, irev) if irev else None
    _gate(gates, "implementation_submitted", "Implementation revision submitted (immutable)",
          bool(irev and irev.status in (RevisionStatus.IN_REVIEW, RevisionStatus.APPROVED)),
          f"Implementation revision status {irev.status if irev else 'missing'}")
    _gate(gates, "implementation_feasibility", "Implementation feasible with a supported evaluator",
          bool(v and v.outcome == ValidationOutcome.PASS and v.evaluator_id),
          {"outcome": v.outcome if v else None, "evaluator": v.evaluator_id if v else None})
    tests = v.test_results if v else []
    _gate(gates, "validation_tests", "Validation tests passed",
          bool(tests) and all(t.get("passed") for t in tests),
          f"{sum(1 for t in tests if t.get('passed'))}/{len(tests)} evaluator tests passed")
    _gate(gates, "integration_verified", "Native details verified against primary provider documentation",
          bool(v and v.integration_ready),
          (v.integration_blockers if v else ["No validation"]) or "Verified")

    refs = manifest["assessment_refs"]
    run = ctx.session.get(m.AssessmentRun, refs[0]["run_id"]) if refs else None
    latest_run = latest_completed_run(ctx, manifest["control"]["revision_id"], impl_ref["revision_id"],
                                      plan.target_scope_id)
    snap = latest_snapshot(ctx, plan.provider)
    detail = []
    ok = run is not None and run.status == AssessmentStatus.COMPLETED
    if run is None:
        detail.append("No assessment for this control/implementation revision and target scope.")
    else:
        if run.status != AssessmentStatus.COMPLETED:
            detail.append("Assessment is UNSUPPORTED (no evaluator).")
        if ctx.now - run.assessed_at > timedelta(hours=fresh["assessment_max_age_hours"]):
            ok = False
            detail.append("Assessment is older than the freshness threshold.")
        if latest_run is not None and latest_run.id != run.id:
            ok = False
            detail.append("A newer assessment exists; resubmit the package.")
        if snap is not None and run.inventory_snapshot_id != snap.id:
            ok = False
            detail.append("A newer inventory snapshot exists; reassess.")
    _gate(gates, "current_assessment", "Current assessment of exact revisions", ok,
          detail or f"Assessment {run.id} at {iso(run.assessed_at)}")

    supported = provider.capabilities().supported_scope_types
    scope_ok, scope_detail = True, []
    for ring in manifest["deploy_rings"]:
        for sid in ring["scope_ids"]:
            s = tree.scopes.get(sid)
            if s is None or s.scope_type not in supported or not tree.is_within(sid, plan.target_scope_id):
                scope_ok = False
                scope_detail.append(f"{sid} unresolved/unsupported")
    _gate(gates, "scope_resolution", "Target scopes resolved", scope_ok and bool(manifest["deploy_rings"]),
          scope_detail or "All ring scopes resolve under the target")

    ring_checks = []
    for ring in manifest["deploy_rings"]:
        ring_checks += [c.to_dict() for c in provider.validate_ring_settings(ring["stage"], ring["settings"], irev)]
    _gate(gates, "native_settings", "Native settings valid for provider capabilities",
          all(c["status"] in ("PASS", "WARN") for c in ring_checks), ring_checks)

    enforcing_scopes = [s for r in manifest["deploy_rings"] if r["enforcing"] for s in r["scope_ids"]]
    addition_resources = {rid for a in manifest["exception_set"]["additions"] for rid in a["resource_ids"]}
    addition_scopes = [a["scope_id"] for a in manifest["exception_set"]["additions"] if not a["resource_ids"]]
    open_blockers, unknown_cfg = [], 0
    if run is not None and run.status == AssessmentStatus.COMPLETED:
        for b in run.blockers:
            sid = b.get("scope_id")
            if not sid or not any(tree.is_within(sid, s) for s in enforcing_scopes):
                continue
            if b["kind"] == "EXCEPTION_NOT_APPLIED" and (
                    b["subject_id"] in addition_resources
                    or any(tree.is_within(sid, s) for s in addition_scopes)):
                continue
            open_blockers.append(b["id"])
        for r in ctx.session.scalars(select(m.AssessmentResult).where(
                m.AssessmentResult.run_id == run.id, m.AssessmentResult.subject_kind == "RESOURCE",
                m.AssessmentResult.configuration_result == "UNKNOWN")):
            if r.scope_id and any(tree.is_within(r.scope_id, s) for s in enforcing_scopes):
                unknown_cfg += 1
    readiness_ok = (run is not None and run.status == AssessmentStatus.COMPLETED
                    and len(open_blockers) <= thresholds["max_open_blockers_in_enforcing_ring"]
                    and unknown_cfg <= thresholds["max_unknown_configuration_in_enforcing_ring"])
    _gate(gates, "readiness", "No open readiness blockers in enforcing rings", readiness_ok,
          {"open_blockers": open_blockers, "unknown_configuration": unknown_cfg,
           "thresholds": thresholds})

    rep_problems = []
    bindings = list(ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider == plan.provider)))
    for exc in ctx.session.scalars(select(m.SecurityException).where(
            m.SecurityException.control_id == plan.control_id)):
        eff = effective_status(exc, ctx.now)
        if eff not in (ExceptionStatus.REQUESTED, ExceptionStatus.SECURITY_REVIEW, ExceptionStatus.APPROVED):
            continue
        if not any(tree.is_within(exc.scope_id, s) or tree.is_within(s, exc.scope_id) for s in enforcing_scopes):
            continue
        rep, reason = exc.representability, exc.representability_reason
        if plan.provider == "aws" and irev is not None:
            r = provider.validate_exception_representation(exc, irev, bindings, tree, ring_scope_ids=enforcing_scopes)
            rep, reason = r.representability, r.reason
        if rep != Representability.REPRESENTABLE:
            rep_problems.append(f"{exc.id} ({eff}): {rep} - {reason}")
        elif eff != ExceptionStatus.APPROVED:
            rep_problems.append(f"{exc.id} is pending a decision ({eff}).")
    _gate(gates, "exceptions_representable", "Exceptions in enforcing rings decided and representable",
          not rep_problems, rep_problems or "All relevant exceptions are representable")

    validity = []
    for a in manifest["exception_set"]["additions"]:
        exc = ctx.session.get(m.SecurityException, a["exception_id"])
        if exc is None or effective_status(exc, ctx.now) != ExceptionStatus.APPROVED:
            validity.append(f"{a['exception_id']} is no longer approved/valid")
    _gate(gates, "exception_validity", "Included exceptions approved and within validity", not validity,
          validity or f"{len(manifest['exception_set']['additions'])} exception addition(s) valid")

    stale = []
    if run is not None:
        max_age = timedelta(days=fresh["readiness_max_age_days"])
        for e in run.inputs.get("readiness_evidence", []):
            ev = ctx.session.get(m.ReadinessEvidence, e["id"])
            if ev and not e["stale"] and ctx.now - ev.collected_at > max_age:
                stale.append(e["id"])
    _gate(gates, "evidence_freshness", "Readiness evidence still fresh", not stale,
          stale or "Evidence used by the assessment is within the freshness window")

    current_baseline = digest(_baseline(ctx, plan))
    _gate(gates, "baseline_unchanged", "Existing native baseline unchanged since package build",
          current_baseline == manifest["baseline"]["digest"],
          {"expected": manifest["baseline"]["digest"], "current": current_baseline})

    rb = manifest.get("rollback_plan") or {}
    _gate(gates, "rollback_instructions", "Rollback/recovery instructions present",
          bool(rb.get("steps")) and bool(rb.get("prior_known_good")) and bool(rb.get("emergency_path")),
          "Rollback is a reviewed change to a prior known-good binding; it does not undo application changes.")

    if package is not None:
        rebuilt = digest(build_manifest(ctx, plan, package.through_stage))
        _gate(gates, "manifest_current", "No material change since submission", rebuilt == package.manifest_digest,
              "Unchanged" if rebuilt == package.manifest_digest else
              "Current state produces a different manifest (plan, revisions, assessment, exceptions or baseline "
              "changed). Submit a new package for reassessment and reapproval.")
        if plan.state == PlanState.CANCELLED:
            _gate(gates, "plan_active", "Rollout plan not cancelled", False, "Plan cancelled")

    if include_approvals and package is not None:
        decisions = [d for d in package_decisions(ctx, package) if d.subject_digest == package.manifest_digest]
        approvals = {d.role: d for d in decisions if d.decision == DecisionValue.APPROVE}
        authors = package_authors(ctx, package, plan)
        sec, eng = approvals.get(Role.SECURITY_APPROVER), approvals.get(Role.CLOUD_ENGINEER)
        _gate(gates, "security_approval", "Security approval of exact digest", sec is not None,
              f"by {sec.actor_id}" if sec else "missing", "APPROVAL")
        _gate(gates, "engineering_approval", "Cloud Engineering readiness approval of exact digest", eng is not None,
              f"by {eng.actor_id}" if eng else "missing", "APPROVAL")
        sod = sec is not None and eng is not None and sec.actor_id != eng.actor_id \
            and sec.actor_id not in authors and eng.actor_id not in authors
        _gate(gates, "separation_of_duties", "Distinct approvers, none of them authors", sod,
              {"authors": sorted(authors)}, "APPROVAL")
    return gates


def failing(gates: list[dict], phase: str | None = None) -> list[dict]:
    return [g for g in gates if g["status"] != "PASS" and (phase is None or g["phase"] == phase)]


# ------------------------------------------------------------------------------------ packages


def get_package(ctx: RequestContext, package_id: str, lock: bool = False) -> m.ChangePackage:
    pkg = ctx.session.get(m.ChangePackage, package_id, with_for_update=lock)
    if pkg is None:
        raise NotFound(f"Change package {package_id} not found")
    get_plan(ctx, pkg.plan_id)
    return pkg


def create_package(ctx: RequestContext, plan_id: str, through_stage: str) -> m.ChangePackage:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Submitting a change package")
    plan = get_plan(ctx, plan_id, lock=True)
    if plan.state == PlanState.CANCELLED:
        raise Conflict("Plan is cancelled.")
    if through_stage not in [r["stage"] for r in plan.rings]:
        raise ValidationFailed(f"Plan has no {through_stage} ring.")
    manifest = build_manifest(ctx, plan, through_stage)
    mdigest = digest(manifest)
    previous = list(ctx.session.scalars(select(m.ChangePackage).where(m.ChangePackage.plan_id == plan.id)
                                        .order_by(m.ChangePackage.package_revision)))
    for p in previous:
        if p.manifest_digest == mdigest and p.status in (PackageStatus.IN_REVIEW, PackageStatus.APPROVED):
            return p
    for p in previous:
        if p.status in (PackageStatus.IN_REVIEW, PackageStatus.APPROVED, PackageStatus.STALE):
            p.status = PackageStatus.SUPERSEDED
            p.status_changed_at = ctx.now
            p.status_reasons = p.status_reasons + [f"Superseded by a new package revision at {iso(ctx.now)}; "
                                                   "earlier decisions are preserved but no longer count."]
            audit.record(ctx, action="change_package.superseded", object_type="change_package", object_id=p.id,
                         object_revision=p.package_revision, control_id=plan.control_id,
                         after_digest=p.manifest_digest)
    pkg = m.ChangePackage(
        id=new_id("pkg"), plan_id=plan.id, control_id=plan.control_id,
        package_revision=(previous[-1].package_revision + 1) if previous else 1, through_stage=through_stage,
        status=PackageStatus.IN_REVIEW, manifest=manifest, manifest_digest=mdigest, gate_results=[],
        status_reasons=[], created_by=ctx.principal.user_id, created_at=ctx.now, status_changed_at=ctx.now)
    ctx.session.add(pkg)
    ctx.session.flush()
    pkg.gate_results = evaluate_gates(ctx, plan, manifest, pkg, include_approvals=True)
    ctx.session.flush()
    audit.record(ctx, action="change_package.submitted", object_type="change_package", object_id=pkg.id,
                 object_revision=pkg.package_revision, control_id=plan.control_id, after_digest=mdigest,
                 scope_ids=[plan.target_scope_id],
                 details={"through_stage": through_stage,
                          "failing_pre_approval_gates": [g["id"] for g in failing(pkg.gate_results, "PRE_APPROVAL")]})
    return pkg


def mark_stale(ctx: RequestContext, pkg: m.ChangePackage, gates: list[dict], when: str) -> None:
    if pkg.status == PackageStatus.APPROVED:
        pkg.status = PackageStatus.STALE
        pkg.status_changed_at = ctx.now
        pkg.status_reasons = pkg.status_reasons + [
            f"Stale at {when} ({iso(ctx.now)}): " + ", ".join(g["id"] for g in failing(gates))]
        pkg.gate_results = gates
        audit.record(ctx, action="change_package.stale", object_type="change_package", object_id=pkg.id,
                     object_revision=pkg.package_revision, control_id=pkg.control_id,
                     after_digest=pkg.manifest_digest, reason=f"Gate recheck failed at {when}",
                     details={"failing": [g["id"] for g in failing(gates)]})


def decide_package(ctx: RequestContext, package_id: str, payload) -> m.ChangePackage:
    role = Role(payload.role)
    require_global_role(ctx.principal, role, action=f"Recording a {role.value} decision")
    pkg = get_package(ctx, package_id, lock=True)
    plan = get_plan(ctx, pkg.plan_id, lock=True)
    if pkg.status != PackageStatus.IN_REVIEW:
        raise Conflict(f"Package is {pkg.status}; decisions are only accepted while IN_REVIEW.")
    if payload.expected_digest != pkg.manifest_digest:
        raise Conflict("The reviewed digest does not match this package. Reload and review the exact package.",
                       code="DIGEST_MISMATCH", details={"package_digest": pkg.manifest_digest})
    authors = package_authors(ctx, pkg, plan)
    if ctx.principal.user_id in authors:
        raise Forbidden("Authors and requesters cannot approve or reject their own change.", code="SELF_APPROVAL")
    decisions = [d for d in package_decisions(ctx, pkg) if d.subject_digest == pkg.manifest_digest]
    if any(d.actor_id == ctx.principal.user_id for d in decisions):
        raise Forbidden("This identity already recorded a decision on this package; one identity cannot satisfy "
                        "more than one required approval.", code="SEPARATION_OF_DUTIES")
    if any(d.role == role and d.decision == DecisionValue.APPROVE for d in decisions):
        raise Conflict(f"{role.value} approval already recorded.")
    gates = evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=False)
    if payload.decision == DecisionValue.APPROVE:
        bad = failing(gates)
        if bad:
            raise GateFailed("Approval refused: gates are failing for this exact package.",
                             details=[{"id": g["id"], "label": g["label"], "detail": g["detail"]} for g in bad])
    d = m.ApprovalDecision(id=new_id("dec"), subject_type="CHANGE_PACKAGE", subject_id=pkg.id,
                           subject_digest=pkg.manifest_digest, actor_id=ctx.principal.user_id, role=role,
                           decision=payload.decision, rationale=payload.rationale, decided_at=ctx.now,
                           correlation_id=audit.current_correlation_id())
    ctx.session.add(d)
    ctx.session.flush()
    if payload.decision == DecisionValue.REJECT:
        pkg.status = PackageStatus.REJECTED
        pkg.status_changed_at = ctx.now
        pkg.status_reasons = pkg.status_reasons + [f"Rejected by {ctx.principal.user_id} ({role.value})"]
    else:
        full = evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=True)
        pkg.gate_results = full
        if not failing(full):
            pkg.status = PackageStatus.APPROVED
            pkg.status_changed_at = ctx.now
            _approve_revisions(ctx, pkg)
    ctx.session.flush()
    audit.record(ctx, action=f"change_package.decision.{payload.decision.lower()}", object_type="change_package",
                 object_id=pkg.id, object_revision=pkg.package_revision, control_id=pkg.control_id,
                 reason=payload.rationale, after_digest=pkg.manifest_digest, scope_ids=[plan.target_scope_id],
                 details={"role": role.value, "decision_id": d.id, "package_status": pkg.status})
    return pkg


def _approve_revisions(ctx: RequestContext, pkg: m.ChangePackage) -> None:
    crev = ctx.session.get(m.ControlRevision, pkg.manifest["control"]["revision_id"])
    if crev.status == RevisionStatus.IN_REVIEW:
        for other in ctx.session.scalars(select(m.ControlRevision).where(
                m.ControlRevision.control_id == crev.control_id, m.ControlRevision.status == RevisionStatus.APPROVED)):
            other.status = RevisionStatus.SUPERSEDED
            other.superseded_at = ctx.now
        crev.status = RevisionStatus.APPROVED
        crev.approved_at = ctx.now
        audit.record(ctx, action="control_revision.approved", object_type="control_revision", object_id=crev.id,
                     object_revision=crev.revision, control_id=crev.control_id, after_digest=crev.content_digest,
                     details={"via_package": pkg.id})
    for i in pkg.manifest["implementations"]:
        irev = ctx.session.get(m.ImplementationRevision, i["revision_id"])
        if irev.status == RevisionStatus.IN_REVIEW:
            for other in ctx.session.scalars(select(m.ImplementationRevision).where(
                    m.ImplementationRevision.implementation_id == irev.implementation_id,
                    m.ImplementationRevision.status == RevisionStatus.APPROVED)):
                other.status = RevisionStatus.SUPERSEDED
                other.superseded_at = ctx.now
            irev.status = RevisionStatus.APPROVED
            irev.approved_at = ctx.now
            audit.record(ctx, action="implementation_revision.approved", object_type="implementation_revision",
                         object_id=irev.id, object_revision=irev.revision, control_id=pkg.control_id,
                         details={"via_package": pkg.id})


def live_gates(ctx: RequestContext, package_id: str) -> list[dict]:
    pkg = get_package(ctx, package_id)
    plan = get_plan(ctx, pkg.plan_id)
    return evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=True)


# ------------------------------------------------------------------------------------ progression


def latest_package(ctx: RequestContext, plan_id: str) -> m.ChangePackage | None:
    return ctx.session.scalars(select(m.ChangePackage).where(m.ChangePackage.plan_id == plan_id)
                               .order_by(m.ChangePackage.package_revision.desc()).limit(1)).first()


def advance(ctx: RequestContext, plan_id: str, payload) -> m.RolloutPlan:
    require_global_role(ctx.principal, Role.CLOUD_ENGINEER, action="Advancing a rollout")
    plan = get_plan(ctx, plan_id, lock=True)
    if plan.lock_version != payload.expected_lock_version:
        raise Conflict("Plan changed since you loaded it.", code="STALE_VERSION")
    if plan.state != PlanState.ACTIVE:
        raise Conflict(f"Plan is {plan.state}; resume it before advancing.")
    ring_stages = [r["stage"] for r in plan.rings]
    later = [s for s in ring_stages if stage_index(s) > stage_index(plan.stage)]
    if not later or payload.to_stage != later[0]:
        raise Conflict(f"Next permitted stage is {later[0] if later else 'none'}; rings cannot be skipped.")
    pkg = latest_package(ctx, plan.id)
    problems: list[str] = []
    if pkg is None or pkg.status != PackageStatus.APPROVED:
        raise GateFailed("Advancing requires an APPROVED, current change package.",
                         details={"package_status": pkg.status if pkg else None})
    if payload.to_stage not in [r["stage"] for r in pkg.manifest["deploy_rings"]]:
        raise GateFailed(f"The approved package does not include the {payload.to_stage} ring.")
    gates = evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=True)
    if failing(gates):
        mark_stale(ctx, pkg, gates, "rollout advance")
        ctx.commit()
        raise GateFailed("Package gates no longer pass; it is now STALE and must be resubmitted.",
                         details=[{"id": g["id"], "detail": g["detail"]} for g in failing(gates)])
    bundle = ctx.session.scalars(select(m.HandoffBundle).where(
        m.HandoffBundle.package_id == pkg.id, m.HandoffBundle.kind == "APPROVED_HANDOFF")).first()
    if bundle is None:
        problems.append("Approved handoff bundle has not been exported.")
    fresh = settings.get(ctx.session, "evidence_freshness")
    obs_max = timedelta(hours=fresh["observation_max_age_hours"])
    min_obs = timedelta(hours=settings.get(ctx.session, "rollout")["min_observation_hours"])
    last_verified = None
    for ring in pkg.manifest["deploy_rings"]:
        if stage_index(ring["stage"]) >= stage_index(payload.to_stage):
            continue
        if ring["artifact_required"]:
            targets = list(ctx.session.scalars(select(m.DeliveryTarget).where(
                m.DeliveryTarget.bundle_id == (bundle.id if bundle else ""),
                m.DeliveryTarget.ring_stage == ring["stage"])))
            for t in targets:
                obs = ctx.session.get(m.Observation, t.last_observation_id) if t.last_observation_id else None
                if t.state != "VERIFIED" or obs is None or ctx.now - obs.observed_at > obs_max:
                    problems.append(f"{ring['stage']} target {t.native_identity} is not freshly VERIFIED "
                                    f"(state {t.state}).")
                elif t.verified_at and (last_verified is None or t.verified_at > last_verified):
                    last_verified = t.verified_at
            if not targets:
                problems.append(f"{ring['stage']} ring has no delivery targets.")
        elif ring["settings"].get("use_existing_binding_id"):
            b = ctx.session.get(m.PolicyBinding, ring["settings"]["use_existing_binding_id"])
            if b is None or b.observed_at is None or ctx.now - b.observed_at > obs_max:
                problems.append(f"Existing observation binding for {ring['stage']} lacks a fresh observation.")
            elif b.observed_state != b.desired_state:
                problems.append(f"Existing observation binding for {ring['stage']} has drifted.")
            else:
                last_verified = max(last_verified or b.observed_at, b.observed_at)
    if last_verified is not None and ctx.now - last_verified < min_obs:
        problems.append(f"Minimum observation period of {min_obs} has not elapsed.")
    if problems:
        raise GateFailed("Progression gates failed.", details=problems)
    before = plan.stage
    plan.stage = payload.to_stage
    plan.state_reason = payload.reason
    plan.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="rollout.advanced", object_type="rollout_plan", object_id=plan.id,
                 control_id=plan.control_id, reason=payload.reason, scope_ids=[plan.target_scope_id],
                 details={"from": before, "to": plan.stage, "package_id": pkg.id,
                          "package_digest": pkg.manifest_digest})
    return plan


def set_state(ctx: RequestContext, plan_id: str, action: str, reason: str, expected_lock_version: int
              ) -> m.RolloutPlan:
    require_any_role(ctx.principal, [Role.SECURITY_APPROVER, Role.CLOUD_ENGINEER], action=f"Rollout {action}")
    plan = get_plan(ctx, plan_id, lock=True)
    if plan.lock_version != expected_lock_version:
        raise Conflict("Plan changed since you loaded it.", code="STALE_VERSION")
    transitions = {"pause": (PlanState.ACTIVE, PlanState.PAUSED), "resume": (PlanState.PAUSED, PlanState.ACTIVE),
                   "cancel": (None, PlanState.CANCELLED)}
    src, dst = transitions[action]
    if plan.state == PlanState.CANCELLED:
        raise Conflict("Plan is already cancelled.")
    if src is not None and plan.state != src:
        raise Conflict(f"Cannot {action} a plan in state {plan.state}.")
    plan.state = dst
    plan.state_reason = reason
    plan.updated_at = ctx.now
    note = None
    if action == "cancel":
        note = ("Cancellation stops further progression only. Already-applied native changes stay in place until a "
                "reviewed rollback package is delivered by Cloud Engineering.")
        for p in ctx.session.scalars(select(m.ChangePackage).where(
                m.ChangePackage.plan_id == plan.id,
                m.ChangePackage.status.in_([PackageStatus.IN_REVIEW, PackageStatus.APPROVED]))):
            p.status = PackageStatus.SUPERSEDED
            p.status_changed_at = ctx.now
            p.status_reasons = p.status_reasons + ["Plan cancelled."]
    ctx.session.flush()
    audit.record(ctx, action=f"rollout.{action}", object_type="rollout_plan", object_id=plan.id,
                 control_id=plan.control_id, reason=reason, scope_ids=[plan.target_scope_id],
                 details={"state": plan.state, "note": note})
    return plan


def _suggest_pilot(ctx: RequestContext, irev: m.ImplementationRevision, candidates: list[str]) -> str:
    """Prefer the candidate with applicable resources and the fewest blockers in the latest assessment."""
    run = ctx.session.scalars(select(m.AssessmentRun).where(
        m.AssessmentRun.implementation_revision_id == irev.id, m.AssessmentRun.status == AssessmentStatus.COMPLETED)
        .order_by(m.AssessmentRun.seq.desc()).limit(1)).first()
    if run is None:
        return candidates[0]
    tree = ctx.tree
    applicable = {r.scope_id for r in ctx.session.scalars(select(m.AssessmentResult).where(
        m.AssessmentResult.run_id == run.id, m.AssessmentResult.applicability == "APPLICABLE"))}

    def score(sid: str) -> tuple[int, int, str]:
        has_resources = any(a and tree.is_within(a, sid) for a in applicable)
        blockers = sum(1 for b in run.blockers if b.get("scope_id") and tree.is_within(b["scope_id"], sid))
        return (0 if has_resources else 1, blockers, sid)

    return min(candidates, key=score)


def plan_template(ctx: RequestContext, control_id: str, implementation_revision_id: str) -> dict[str, Any]:
    """Suggested (not persisted) plan derived from provider capabilities and existing bindings."""
    irev = get_impl_revision(ctx, implementation_revision_id)
    impl = irev.implementation
    provider = get_provider(impl.provider)
    tree = ctx.tree
    roots = [s for s in tree.scopes.values() if s.provider == impl.provider and s.parent_id is None]
    target = roots[0].id if roots else None
    rings: list[dict] = []
    if impl.provider == "azure":
        existing = ctx.session.scalars(select(m.PolicyBinding).where(
            m.PolicyBinding.provider == "azure", m.PolicyBinding.definition_ref.ilike(irev.source_ref))).first()
        if existing:
            rings.append({"stage": "OBSERVATION", "scope_ids": [existing.target_scope_id],
                          "settings": {"use_existing_binding_id": existing.id}})
        else:
            rings.append({"stage": "OBSERVATION", "scope_ids": [target],
                          "settings": {"effect": "Audit", "enforcementMode": "Default"}})
        subs = sorted(s.id for s in tree.scopes.values() if s.scope_type == "AZURE_SUBSCRIPTION")
        if subs:
            rings.append({"stage": "PILOT", "scope_ids": [_suggest_pilot(ctx, irev, subs)],
                          "settings": {"effect": "Deny", "enforcementMode": "Default"}})
    else:
        rings.append({"stage": "OBSERVATION", "scope_ids": [target], "settings": {"mode": "OFFLINE_ASSESSMENT"}})
        accts = sorted(s.id for s in tree.scopes.values() if s.scope_type == "AWS_ACCOUNT"
                       and not s.is_management_account)
        if accts:
            rings.append({"stage": "PILOT", "scope_ids": [_suggest_pilot(ctx, irev, accts)], "settings": {"attach": True}})
    return {
        "control_id": control_id, "implementation_revision_id": irev.id, "target_scope_id": target,
        "title": f"{impl.name} rollout", "rings": rings,
        "pause_criteria": ["Unexpected denied requests reported by an application owner",
                           "Observation shows drift from the approved native settings",
                           "Any delivery receipt reports FAILED"],
        "rollback_plan": {
            "prior_known_good": "Pre-change state recorded in baseline-comparison.json (existing bindings only).",
            "steps": ["Cloud Engineering submits a reviewed change removing the new assignment/attachment at the "
                      "affected ring scope (or reverting its effect to the prior known-good value).",
                      "Confirm removal through a fresh observation before closing the incident."],
            "limitations": ["Rollback does not undo application changes or remediation already performed.",
                            "Recovery is not immediate; it depends on Cloud Engineering's pipeline."],
            "emergency_path": "Cloud Engineering's documented break-glass change procedure for policy "
                              "infrastructure (owned by Cloud Engineering; no bypass role exists in this platform).",
        },
        "prerequisite_changes": [],
        "deployment_instructions": "Apply rings in order. Within a ring, apply exemptions before enforcing "
                                   "assignments/attachments. Report one receipt per target scope.",
        "capability_notes": provider.capabilities().ring_settings,
    }
