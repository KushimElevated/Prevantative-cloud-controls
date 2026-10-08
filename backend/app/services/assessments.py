"""Impact Assessment.

Three questions are kept separate:
1. Current configuration: which resources meet the stated configuration requirement?
2. Request impact: which supplied representative requests would the supported model reject?
3. Operational readiness: which applications supplied evidence they can operate under the intended state?

Assessment reads fixtures only and never modifies infrastructure. A revision without a matching
versioned evaluator receives an UNSUPPORTED run with no results, never a passing one.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select

from app.auth.principal import require_role
from app.core.clock import iso
from app.core.digests import digest
from app.core.errors import NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import (
    Applicability,
    AssessmentStatus,
    ConfigurationResult,
    ExceptionDisposition,
    Readiness,
    RequestImpact,
    Role,
)
from app.providers.base import AssessmentContext
from app.providers.common import EvidenceView, is_stale
from app.providers.registry import get_provider
from app.services import audit, settings
from app.services.context import RequestContext
from app.services.controls import get_revision as get_control_revision
from app.services.exceptions import DISPOSITION_PRIORITY, disposition, effective_status, ensure_work
from app.services.implementations import get_revision as get_impl_revision
from app.services.views import run_view, work_view

EVALUATION_FRAMEWORK_VERSION = "1.0.0"


def latest_snapshot(ctx: RequestContext, provider: str) -> m.InventorySnapshot | None:
    return ctx.session.scalars(select(m.InventorySnapshot).where(m.InventorySnapshot.provider == provider)
                               .order_by(m.InventorySnapshot.collected_at.desc()).limit(1)).first()


def latest_fixture_set(ctx: RequestContext, provider: str) -> m.RequestFixtureSet | None:
    return ctx.session.scalars(select(m.RequestFixtureSet).where(m.RequestFixtureSet.provider == provider)
                               .order_by(m.RequestFixtureSet.created_at.desc()).limit(1)).first()


def _evidence_index(ctx: RequestContext, control_id: str, max_age: timedelta
                    ) -> list[tuple[m.ReadinessEvidence, bool]]:
    rows = ctx.session.scalars(select(m.ReadinessEvidence).where(m.ReadinessEvidence.control_id == control_id)
                               .order_by(m.ReadinessEvidence.collected_at.desc(), m.ReadinessEvidence.id)).all()
    return [(e, is_stale(e.collected_at, ctx.now, max_age)) for e in rows if e.collected_at <= ctx.now]


def _evidence_for(resource: m.ResourceSnapshot, index: list[tuple[m.ReadinessEvidence, bool]], tree
                  ) -> dict[str, EvidenceView]:
    out: dict[str, EvidenceView] = {}
    for e, stale in index:  # newest first
        if e.prerequisite in out:
            continue
        if not tree.is_within(resource.scope_id, e.scope_id):
            continue
        if e.application != "*" and e.application != resource.application:
            continue
        out[e.prerequisite] = EvidenceView(e.id, e.prerequisite, e.status, e.collected_at, stale, e.summary,
                                           e.evidence_ref, e.provided_by)
    return out


def _proposed_binding_identities(ctx: RequestContext, implementation_id: str) -> set[str]:
    rows = ctx.session.execute(
        select(m.PolicyBinding.native_id).join(
            m.ImplementationRevision, m.ImplementationRevision.id == m.PolicyBinding.implementation_revision_id)
        .where(m.ImplementationRevision.implementation_id == implementation_id)).all()
    return {r[0].lower() for r in rows}


def run_assessment(ctx: RequestContext, payload) -> m.AssessmentRun:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Running an impact assessment")
    impl_rev = get_impl_revision(ctx, payload.implementation_revision_id)
    impl = impl_rev.implementation
    control_rev = get_control_revision(ctx, payload.control_revision_id or impl_rev.control_revision_id)
    if control_rev.control_id != impl.control_id:
        raise ValidationFailed("Control revision and implementation belong to different controls.")
    provider = get_provider(impl.provider)
    tree = ctx.tree
    target = tree.get(payload.target_scope_id)
    if target.provider != impl.provider:
        raise ValidationFailed("Target scope provider does not match the implementation provider.")
    if target.scope_type not in provider.capabilities().supported_scope_types:
        raise ValidationFailed(f"Scope type {target.scope_type} is not supported by {impl.provider}.")
    snapshot = (ctx.session.get(m.InventorySnapshot, payload.inventory_snapshot_id)
                if payload.inventory_snapshot_id else latest_snapshot(ctx, impl.provider))
    if snapshot is None or snapshot.provider != impl.provider:
        raise ValidationFailed("No inventory snapshot for this provider.")
    fixture_set = None
    if payload.use_request_fixtures:
        fixture_set = (ctx.session.get(m.RequestFixtureSet, payload.request_fixture_set_id)
                       if payload.request_fixture_set_id else latest_fixture_set(ctx, impl.provider))
        if fixture_set is not None and fixture_set.provider != impl.provider:
            raise ValidationFailed("Request fixture set provider mismatch.")

    freshness = settings.get(ctx.session, "evidence_freshness")
    readiness_max = timedelta(days=freshness["readiness_max_age_days"])
    obs_max = timedelta(hours=freshness["observation_max_age_hours"])
    inv_max = timedelta(hours=freshness["inventory_max_age_hours"])

    target_ids = set(tree.descendants(target.id))
    resources = list(ctx.session.scalars(select(m.ResourceSnapshot).where(
        m.ResourceSnapshot.snapshot_id == snapshot.id).order_by(m.ResourceSnapshot.resource_id)))
    resources = [r for r in resources if r.scope_id in target_ids]
    bindings = list(ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider == impl.provider)
                                        .order_by(m.PolicyBinding.id)))
    exceptions = list(ctx.session.scalars(select(m.SecurityException).where(
        m.SecurityException.control_id == control_rev.control_id).order_by(m.SecurityException.id)))
    evidence_index = _evidence_index(ctx, control_rev.control_id, readiness_max)
    evaluator = provider.evaluator_for(impl_rev)

    inputs = {
        "framework_version": EVALUATION_FRAMEWORK_VERSION,
        "control_revision": {"id": control_rev.id, "revision": control_rev.revision, "status": control_rev.status},
        "implementation_revision": {"id": impl_rev.id, "revision": impl_rev.revision,
                                    "content_digest": impl_rev.content_digest, "parameters": impl_rev.parameters,
                                    "assignment_settings": impl_rev.assignment_settings},
        "target_scope": {"id": target.id, "native_id": target.native_id, "resolved_scope_ids": sorted(target_ids)},
        "inventory_snapshot": {"id": snapshot.id, "content_digest": snapshot.content_digest,
                               "collected_at": iso(snapshot.collected_at), "provenance": snapshot.provenance},
        "request_fixture_set": ({"id": fixture_set.id, "content_digest": fixture_set.content_digest,
                                 "provenance": fixture_set.provenance} if fixture_set else None),
        "bindings": [{"id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                      "state_digest": digest({"desired": b.desired_state, "observed": b.observed_state}),
                      "observed_at": iso(b.observed_at)} for b in bindings],
        "exceptions": [{"id": e.id, "effective_status": effective_status(e, ctx.now),
                        "native_status": e.native_status, "expires_at": iso(e.expires_at),
                        "representability": e.representability} for e in exceptions],
        "readiness_evidence": [{"id": e.id, "collected_at": iso(e.collected_at), "stale": stale}
                               for e, stale in evidence_index],
        "assessed_at": iso(ctx.now),
    }

    run = m.AssessmentRun(
        id=new_id("asmt"), control_id=control_rev.control_id, control_revision_id=control_rev.id,
        implementation_revision_id=impl_rev.id, target_scope_id=target.id, inventory_snapshot_id=snapshot.id,
        request_fixture_set_id=fixture_set.id if fixture_set else None, assessed_at=ctx.now,
        created_by=ctx.principal.user_id, inputs=inputs, input_digest=digest(inputs),
        limitations=list(provider.capabilities().assessment_limitations)
        + ["Assessment reads fixtures only and never modifies infrastructure."],
        assumptions=[], next_steps=[], confidence={}, blockers=[], rollup={}, result_digest="", disclosure="",
        status=AssessmentStatus.COMPLETED, evaluator_id=None, evaluator_version=None)

    if evaluator is None:
        checks = provider.validate_implementation(impl_rev, tree, bindings).checks
        run.status = AssessmentStatus.UNSUPPORTED
        run.rollup = {"status": "UNSUPPORTED",
                      "reason": "No versioned evaluator supports this exact implementation revision. No configuration, "
                                "request or readiness results are claimed.",
                      "checks": [c.to_dict() for c in checks if c.status != "PASS"]}
        run.blockers = [{"id": f"UNSUPPORTED_IMPLEMENTATION:{impl_rev.id}", "kind": "UNSUPPORTED_IMPLEMENTATION",
                         "subject_id": impl_rev.id, "scope_id": target.id, "application": None,
                         "message": "Implementation is not supported by any evaluator; assessment cannot pass.",
                         "resolution": "Use a supported, verified implementation or add a reviewed evaluator.",
                         "owner_team": "Cloud Security"}]
        run.disclosure = ("No evaluation was performed: the implementation revision does not match any supported "
                          "evaluator (policy kind, definition id, version, content digest and parameters must all "
                          "match). This is not a pass.")
        run.confidence = {"configuration": {"category": "NONE", "reasons": ["No evaluator."]}}
        run.result_digest = digest(run.rollup)
        ctx.session.add(run)
        ctx.session.flush()
        audit.record(ctx, action="assessment.unsupported", object_type="assessment_run", object_id=run.id,
                     control_id=run.control_id, scope_ids=[target.id], after_digest=run.result_digest,
                     details={"implementation_revision_id": impl_rev.id})
        return run

    run.evaluator_id, run.evaluator_version = evaluator
    context = AssessmentContext(tree=tree, target_scope_id=target.id, target_scope_ids=target_ids,
                                bindings=bindings, now=ctx.now)
    evaluations = provider.assess_inventory(impl_rev, resources, context)
    proposed_ids = _proposed_binding_identities(ctx, impl.id)

    results: list[m.AssessmentResult] = []
    blockers: dict[str, dict] = {}
    readiness_by_app: dict[str, list[str]] = defaultdict(list)
    exempt_for_proposed: set[str] = set()
    stale_evidence_ids: set[str] = set()
    for r in resources:
        ev = evaluations[r.resource_id]
        covering = [e for e in exceptions if provider.exception_covers_resource(e, r, tree)]
        disps = sorted(((disposition(e, ctx.now), e) for e in covering),
                       key=lambda t: DISPOSITION_PRIORITY.index(t[0]))
        disp, chosen = disps[0] if disps else (ExceptionDisposition.NONE, None)
        covers_proposed = False
        if chosen is not None and disp == ExceptionDisposition.EFFECTIVE:
            pa = ((chosen.native_observed or {}).get("properties") or {}).get("policyAssignmentId")
            covers_proposed = bool(pa and pa.lower() in proposed_ids) or impl.provider == "aws"
            if covers_proposed:
                exempt_for_proposed.add(r.resource_id)
        evidence = _evidence_for(r, evidence_index, tree)
        stale_evidence_ids.update(v.id for v in evidence.values() if v.stale)
        readiness, rreasons, rblockers = provider.readiness_for_resource(
            r, ev, evidence, {"disposition": disp, "covers_proposed": covers_proposed}, context)
        for b in rblockers:
            blockers.setdefault(b.id, b.to_dict())
        if readiness is not None:
            readiness_by_app[r.application or "(unknown application)"].append(readiness)
        if ev.applicability == Applicability.NOT_APPLICABLE:
            disp_out = None
        else:
            disp_out = disp
        baseline = provider.baseline_for_scope(r.scope_id, r.resource_id, bindings, tree, ctx.now, obs_max)
        results.append(m.AssessmentResult(
            id=new_id("ares"), run_id=run.id, subject_kind="RESOURCE", subject_id=r.resource_id,
            subject_name=r.name, resource_type=r.resource_type, scope_id=r.scope_id, application=r.application,
            owner=r.owner, applicability=ev.applicability, configuration_result=ev.configuration_result,
            exception_disposition=disp_out, exception_id=chosen.id if chosen is not None and disp_out else None,
            request_impact=None, readiness=readiness,
            reasons=ev.reasons + rreasons + [f"Exception {e.id}: {d}" for d, e in disps],
            evidence={**ev.evidence, "readiness_evidence": {k: v.to_dict() for k, v in evidence.items()},
                      "enforcement_applies": ev.enforcement_applies,
                      "blockers": [b.id for b in rblockers], "covers_proposed": covers_proposed},
            missing_fields=sorted(set(ev.missing_fields) | set(r.missing_fields or [])), baseline=baseline))

    resource_lookup = {r.resource_id: r for r in resources}
    requests = list(fixture_set.items) if fixture_set else []
    for req in requests:
        rev_ = provider.evaluate_supported_request_fixture(impl_rev, req, context, resource_lookup,
                                                            exempt_for_proposed)
        results.append(m.AssessmentResult(
            id=new_id("ares"), run_id=run.id, subject_kind="REQUEST", subject_id=req.get("id", "?"),
            subject_name=req.get("description", req.get("id", "?"))[:256],
            resource_type=req.get("resource_type") or req.get("action"),
            scope_id=req.get("scope_id") or req.get("account_scope_id"), application=req.get("application"),
            owner=None, applicability=None, configuration_result=None, exception_disposition=None,
            exception_id=None, request_impact=rev_.impact, readiness=None, reasons=rev_.reasons,
            evidence={**rev_.evidence, "request": req}, missing_fields=[], baseline=rev_.baseline))

    run.rollup = _rollup(results, resources, snapshot, fixture_set, readiness_by_app, blockers, tree, ctx, inv_max,
                         stale_evidence_ids, impl_rev)
    run.blockers = sorted(blockers.values(), key=lambda b: b["id"])
    run.confidence = _confidence(run.rollup, snapshot, fixture_set, ctx, inv_max)
    run.assumptions = _assumptions(impl.provider)
    run.next_steps = _next_steps(run.rollup, run.blockers, fixture_set)
    run.disclosure = (
        f"Evaluated {len(resources)} resources from inventory snapshot '{snapshot.label}' "
        f"({snapshot.provenance}, collected {iso(snapshot.collected_at)}) and "
        f"{len(requests)} representative request fixtures"
        f"{' from ' + repr(fixture_set.label) if fixture_set else ' (none supplied)'} using evaluator "
        f"{run.evaluator_id} v{run.evaluator_version}. No cloud API was called, no end-to-end authorization was "
        "simulated, and no network tests were run. Request results cover only the supplied fixtures.")
    run.result_digest = digest({"rollup": run.rollup, "results": [
        {"k": r.subject_kind, "s": r.subject_id, "c": r.configuration_result, "e": r.exception_disposition,
         "i": r.request_impact, "r": r.readiness} for r in results]})
    ctx.session.add(run)
    ctx.session.flush()  # runs are append-only: insert the complete run before its results
    ctx.session.add_all(results)
    ctx.session.flush()
    audit.record(ctx, action="assessment.completed", object_type="assessment_run", object_id=run.id,
                 control_id=run.control_id, scope_ids=[target.id], after_digest=run.result_digest,
                 details={"configuration": run.rollup["configuration"]["counts"],
                          "blockers": len(run.blockers), "evaluator": run.evaluator_id})
    return run


def _rollup(results, resources, snapshot, fixture_set, readiness_by_app, blockers, tree, ctx, inv_max,
            stale_evidence_ids, impl_rev) -> dict[str, Any]:
    res_rows = [r for r in results if r.subject_kind == "RESOURCE"]
    req_rows = [r for r in results if r.subject_kind == "REQUEST"]
    cfg = Counter(r.configuration_result for r in res_rows)
    config_counts = {k.value: cfg.get(k.value, 0) for k in ConfigurationResult}
    total = len(res_rows)
    assert sum(config_counts.values()) == total, "configuration counts must reconcile"
    applicability = Counter(r.applicability for r in res_rows)
    applicable = [r for r in res_rows if r.applicability == Applicability.APPLICABLE]
    exc_counts = Counter(r.exception_disposition for r in applicable)
    nc_by_disp = Counter(r.exception_disposition for r in applicable
                         if r.configuration_result == ConfigurationResult.NON_COMPLIANT)
    readiness_counts = Counter(r.readiness or "NOT_EVALUATED" for r in res_rows)
    by_app = {}
    for app, states in sorted(readiness_by_app.items()):
        by_app[app] = (Readiness.BLOCKED if Readiness.BLOCKED in states
                       else Readiness.UNKNOWN if Readiness.UNKNOWN in states else Readiness.READY)
    accounts: dict[str, Counter] = defaultdict(Counter)
    for r in applicable:
        acct = tree.account_or_subscription(r.scope_id) or r.scope_id
        accounts[acct][r.configuration_result] += 1
    missing_owner = [{"resource_id": r.subject_id, "missing": [f for f, v in (("application", r.application),
                                                                              ("owner", r.owner)) if not v]}
                     for r in applicable if not r.application or not r.owner]
    missing_data = [{"resource_id": r.subject_id, "missing_fields": r.missing_fields}
                    for r in res_rows if r.missing_fields]
    baseline_counts = Counter(r.baseline.get("summary", "NONE") for r in applicable)
    if req_rows:
        impacts = Counter(r.request_impact for r in req_rows)
        request_rollup = {
            "evidence_present": True, "fixture_set_id": fixture_set.id if fixture_set else None,
            "total_requests_evaluated": len(req_rows),
            "counts": {k.value: impacts.get(k.value, 0) for k in RequestImpact},
            "note": "Covers only the supplied representative requests. Future requests are unknown; "
                    "'not denied by this control' is not proof of overall authorization.",
        }
    else:
        request_rollup = {
            "evidence_present": False, "fixture_set_id": None, "total_requests_evaluated": 0,
            "counts": None, "potentially_blocked_operations": "UNKNOWN",
            "note": "No request evidence was supplied. Potentially blocked operations are UNKNOWN - not zero, and "
                    "not the number of non-compliant resources.",
        }
    affected_apps = sorted({r.application or "(unknown application)" for r in applicable
                            if r.configuration_result != ConfigurationResult.COMPLIANT
                            or r.readiness != Readiness.READY})
    return {
        "status": "COMPLETED",
        "configuration": {
            "counts": config_counts, "total_resources_evaluated": total,
            "reconciles": sum(config_counts.values()) == total,
            "definition": "Mutually exclusive configuration results per resource in the target scope and its "
                          "descendants; counts sum to total_resources_evaluated.",
        },
        "applicability": {k.value: applicability.get(k.value, 0) for k in Applicability},
        "exceptions": {
            "counts": {k.value: exc_counts.get(k.value, 0) for k in ExceptionDisposition},
            "non_compliant_by_disposition": {k.value: nc_by_disp.get(k.value, 0) for k in ExceptionDisposition},
            "note": "Reported separately from configuration. An approved exception does not make a failing "
                    "configuration compliant.",
        },
        "request_impact": request_rollup,
        "readiness": {
            "resource_counts": {k: readiness_counts.get(k, 0) for k in
                                ["READY", "BLOCKED", "UNKNOWN", "NOT_EVALUATED"]},
            "by_application": by_app,
            "open_blockers": len(blockers),
        },
        "affected_applications": affected_apps,
        "accounts_subscriptions": {k: dict(v) for k, v in sorted(accounts.items())},
        "missing_owners": missing_owner,
        "missing_or_stale_data": {
            "resources_with_missing_fields": missing_data,
            "stale_readiness_evidence_ids": sorted(stale_evidence_ids),
            "inventory_snapshot_stale": is_stale(snapshot.collected_at, ctx.now, inv_max),
        },
        "baseline_vs_proposed": {
            "baseline_coverage_counts": dict(sorted(baseline_counts.items())),
            "proposed": {"parameters": impl_rev.parameters, "assignment_settings": impl_rev.assignment_settings},
            "newly_preventive_resources": sum(1 for r in applicable if r.baseline.get("summary") in (
                "NONE", "AUDIT_ONLY", "PARTIAL_ACTION_COVERAGE")),
            "note": "Baseline uses existing bindings (desired/observed state). Coverage by an existing binding is "
                    "only 'observed' when a fresh observation exists.",
        },
    }


def _confidence(rollup, snapshot, fixture_set, ctx, inv_max) -> dict[str, Any]:
    counts = rollup["configuration"]["counts"]
    applicable = rollup["applicability"]["APPLICABLE"]
    unknown = counts["UNKNOWN"]
    reasons = []
    if snapshot.provenance != "LIVE":
        reasons.append(f"Inventory provenance is {snapshot.provenance} (not live).")
    if is_stale(snapshot.collected_at, ctx.now, inv_max):
        reasons.append("Inventory snapshot is older than the freshness threshold.")
    if unknown:
        reasons.append(f"{unknown} applicable resource(s) have unknown configuration.")
    cat = "LOW" if (unknown and applicable and unknown * 4 > applicable) or is_stale(
        snapshot.collected_at, ctx.now, inv_max) else ("MEDIUM" if reasons else "HIGH")
    req = rollup["request_impact"]
    if not req["evidence_present"]:
        req_conf = {"category": "NONE", "reasons": ["No request fixtures supplied; impact on operations unknown."]}
    else:
        req_conf = {"category": "LOW", "reasons": ["Representative fixtures only; actual future requests are not "
                                                   "known and inventory cannot predict them."]}
    rc = rollup["readiness"]["resource_counts"]
    evaluated = rc["READY"] + rc["BLOCKED"] + rc["UNKNOWN"]
    if evaluated == 0:
        rd = {"category": "NONE", "reasons": ["No applicable resources evaluated."]}
    elif rc["UNKNOWN"] == 0:
        rd = {"category": "MEDIUM", "reasons": ["Every evaluated resource has conclusive supplied evidence "
                                                "(evidence is attested, not independently tested)."]}
    else:
        rd = {"category": "LOW", "reasons": [f"{rc['UNKNOWN']} resource(s) lack conclusive or fresh evidence."]}
    return {"configuration": {"category": cat, "reasons": reasons or ["Complete, fresh configuration data."]},
            "request_impact": req_conf, "readiness": rd,
            "method": "Rule-based categories from evidence completeness, provenance and freshness. No percentages."}


def _assumptions(provider: str) -> list[str]:
    common = ["Fixture inventory reflects the configuration of the named resources at its collection time.",
              "Readiness evidence is attested by the named team; the platform does not run network tests."]
    if provider == "azure":
        return common + ["The assignment would be created at the target scope with the implementation's effect and "
                         "enforcementMode; ring-specific settings are applied per rollout ring."]
    return common + ["The SCP would be attached at the target scope; inherited SCPs are taken from recorded bindings.",
                     "Principals are evaluated only on their ARN and type as supplied in fixtures."]


def _next_steps(rollup, blockers, fixture_set) -> list[str]:
    steps = []
    kinds = Counter(b["kind"] for b in blockers)
    labels = {
        "EXISTING_VIOLATION": "Route remediation of existing violations to owning teams (linked work items).",
        "EVIDENCE_MISSING": "Collect missing readiness evidence from application teams.",
        "EVIDENCE_STALE": "Refresh stale readiness evidence.",
        "EVIDENCE_NOT_SATISFIED": "Complete unsatisfied prerequisites (private endpoint/DNS/connectivity).",
        "EVIDENCE_UNKNOWN": "Replace inconclusive readiness evidence.",
        "CONFIGURATION_UNKNOWN": "Collect missing configuration from an authoritative inventory source.",
        "MISSING_OWNER": "Identify owners for resources without ownership.",
        "MISSING_APPLICATION": "Identify the owning application for unowned resources.",
        "EXCEPTION_PENDING": "Decide pending exception requests.",
        "EXCEPTION_NOT_APPLIED": "Include approved exceptions as exemptions in the change package.",
        "PREREQUISITE_INSECURE": "Establish the protective baseline before proposing the SCP.",
        "PREREQUISITE_MISSING": "Verify the protective baseline with provenance.",
    }
    for kind, n in kinds.most_common():
        steps.append(f"{labels.get(kind, kind)} ({n})")
    if not rollup["request_impact"]["evidence_present"]:
        steps.append("Supply representative create/update request fixtures to estimate request impact.")
    if not steps:
        steps.append("No blockers in the evaluated scope. Proceed to a rollout plan and change package.")
    return steps


def get_run(ctx: RequestContext, run_id: str) -> m.AssessmentRun:
    run = ctx.session.get(m.AssessmentRun, run_id)
    if run is None:
        raise NotFound(f"Assessment {run_id} not found")
    return run


def filtered_run_view(ctx: RequestContext, run: m.AssessmentRun) -> dict[str, Any]:
    """Scoped users see rollups only when they can read the whole target scope."""
    view = run_view(run)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    if readable is not None and run.target_scope_id not in readable:
        view["rollup"] = {"status": run.status, "redacted": "Rollup spans scopes you are not authorised to read."}
        view["blockers"] = [b for b in run.blockers if b.get("scope_id") in readable]
        view["inputs"] = {"redacted": True}
    return view


def list_results(ctx: RequestContext, run_id: str, *, subject_kind: str | None, filters: dict[str, str | None],
                 limit: int, offset: int) -> tuple[list[m.AssessmentResult], int]:
    get_run(ctx, run_id)
    q = select(m.AssessmentResult).where(m.AssessmentResult.run_id == run_id)
    if subject_kind:
        q = q.where(m.AssessmentResult.subject_kind == subject_kind)
    for col, val in filters.items():
        if val is not None:
            q = q.where(getattr(m.AssessmentResult, col) == val)
    readable = ctx.principal.readable_scope_ids(ctx.tree)
    if readable is not None:
        q = q.where(m.AssessmentResult.scope_id.in_(readable))
    total = ctx.session.scalar(select(func.count()).select_from(q.subquery()))
    rows = list(ctx.session.scalars(q.order_by(m.AssessmentResult.subject_kind, m.AssessmentResult.subject_id)
                                    .limit(limit).offset(offset)))
    return rows, total


def latest_completed_run(ctx: RequestContext, control_revision_id: str, implementation_revision_id: str,
                         target_scope_id: str) -> m.AssessmentRun | None:
    return ctx.session.scalars(select(m.AssessmentRun).where(
        m.AssessmentRun.control_revision_id == control_revision_id,
        m.AssessmentRun.implementation_revision_id == implementation_revision_id,
        m.AssessmentRun.target_scope_id == target_scope_id)
        .order_by(m.AssessmentRun.seq.desc()).limit(1)).first()


def route_blockers(ctx: RequestContext, run_id: str) -> list[dict[str, Any]]:
    """Create lightweight work references for blockers owned by other teams. Idempotent."""
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Routing blockers")
    run = get_run(ctx, run_id)
    created = []
    kind_map = {"EXISTING_VIOLATION": "REMEDIATION", "PREREQUISITE_INSECURE": "REMEDIATION"}
    for b in run.blockers:
        if b["kind"] == "UNSUPPORTED_IMPLEMENTATION":
            continue
        w, new = ensure_work(ctx.session, ctx.now, kind=kind_map.get(b["kind"], "READINESS"),
                             owner_team=b["owner_team"], title=b["message"][:500],
                             detail=f"Resolution: {b['resolution']} (assessment {run.id})",
                             related_type="control", related_id=run.control_id, scope_id=b.get("scope_id"),
                             dedupe_key=f"blocker:{run.control_id}:{b['id']}", created_by=ctx.principal.user_id)
        if new:
            created.append(work_view(w))
    audit.record(ctx, action="assessment.blockers_routed", object_type="assessment_run", object_id=run.id,
                 control_id=run.control_id, details={"created": len(created)})
    return created
