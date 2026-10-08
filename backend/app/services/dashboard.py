"""Decision metrics computed from persisted data. Every ratio names its denominator; empty
denominators are N/A. Combined figures count control-resource pairs, not unique resources."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from sqlalchemy import select

from app.core.clock import iso
from app.models import entities as m
from app.models.enums import (
    STAGE_ORDER,
    Applicability,
    AssessmentStatus,
    ConfigurationResult,
    DeliveryState,
    ExceptionDisposition,
    PackageStatus,
    RevisionStatus,
    RolloutStage,
)
from app.services import settings
from app.services.context import RequestContext
from app.services.exceptions import disposition, effective_status


def ratio(num: int, den: int) -> dict[str, Any]:
    return {"numerator": num, "denominator": den,
            "value": "N/A" if den == 0 else f"{num}/{den}",
            "percent": None if den == 0 else round(100 * num / den)}


def latest_run_for_control(ctx: RequestContext, control_id: str) -> m.AssessmentRun | None:
    return ctx.session.scalars(select(m.AssessmentRun).where(
        m.AssessmentRun.control_id == control_id, m.AssessmentRun.status == AssessmentStatus.COMPLETED)
        .order_by(m.AssessmentRun.seq.desc()).limit(1)).first()


def resource_coverage(ctx: RequestContext, control_id: str, run: m.AssessmentRun) -> dict[str, Any]:
    tree = ctx.tree
    fresh = settings.get(ctx.session, "evidence_freshness")
    obs_max = timedelta(hours=fresh["observation_max_age_hours"])
    readable = ctx.principal.readable_scope_ids(tree)
    targets = list(ctx.session.scalars(select(m.DeliveryTarget).where(
        m.DeliveryTarget.control_id == control_id, m.DeliveryTarget.enforcing.is_(True))))
    t_info = []
    for t in targets:
        obs = ctx.session.get(m.Observation, t.last_observation_id) if t.last_observation_id else None
        fresh_obs = obs is not None and ctx.now - obs.observed_at <= obs_max
        t_info.append((t, fresh_obs))
    rows = list(ctx.session.scalars(select(m.AssessmentResult).where(
        m.AssessmentResult.run_id == run.id, m.AssessmentResult.subject_kind == "RESOURCE")))
    if readable is not None:
        rows = [r for r in rows if r.scope_id in readable]
    counts = Counter()
    baseline_counts = Counter()
    per_resource = []
    for r in rows:
        if r.applicability == Applicability.UNKNOWN:
            counts["unknown_applicability"] += 1
            continue
        if r.applicability != Applicability.APPLICABLE:
            counts["not_applicable"] += 1
            continue
        counts["known_applicable"] += 1
        baseline_counts[(r.baseline or {}).get("summary", "NONE")] += 1
        if r.configuration_result == ConfigurationResult.NON_COMPLIANT:
            counts["remaining_noncompliant"] += 1
        if r.configuration_result == ConfigurationResult.UNKNOWN:
            counts["unknown_configuration"] += 1
        if not r.owner or not r.application:
            counts["missing_owner"] += 1
        exc = ctx.session.get(m.SecurityException, r.exception_id) if r.exception_id else None
        current_disposition = disposition(exc, ctx.now) if exc is not None else r.exception_disposition
        if not r.evidence.get("enforcement_applies", True):
            state = "NOT_ENFORCEABLE_BY_MECHANISM"
        elif current_disposition == ExceptionDisposition.EFFECTIVE:
            state = "EFFECTIVE_EXEMPTION"
        else:
            covering = [(t, f) for t, f in t_info if r.scope_id and tree.is_within(r.scope_id, t.scope_id)]
            baseline = (r.baseline or {}).get("summary")
            if any(t.state == DeliveryState.VERIFIED and f for t, f in covering) or baseline in (
                    "DENY_OBSERVED",):
                state = "VERIFIED_PROTECTED"
            elif covering or baseline in ("DENY_UNVERIFIED",):
                state = "UNKNOWN_ENFORCEMENT"
            else:
                state = "NOT_ENFORCED"
        counts[state.lower()] += 1
        per_resource.append({"resource_id": r.subject_id, "state": state})
    ka = counts["known_applicable"]
    return {
        "assessment_run_id": run.id,
        "assessed_at": iso(run.assessed_at),
        "known_applicable_resources": ka,
        "verified_protected_resources": counts["verified_protected"],
        "verified_protected_ratio": ratio(counts["verified_protected"], ka),
        "effective_exemptions": counts["effective_exemption"],
        "unknown_enforcement": counts["unknown_enforcement"],
        "not_enforced": counts["not_enforced"],
        "not_enforceable_by_mechanism": counts["not_enforceable_by_mechanism"],
        "unknown_applicability": counts["unknown_applicability"],
        "remaining_noncompliant": counts["remaining_noncompliant"],
        "unknown_configuration": counts["unknown_configuration"],
        "missing_owner": counts["missing_owner"],
        "denominator": "Resources in the latest completed assessment whose applicability is known and APPLICABLE "
                       "(exceptions stay in the denominator).",
        "baseline_counts": dict(baseline_counts),
        "per_resource": per_resource,
    }


def control_lifecycle(ctx: RequestContext, control: m.Control) -> dict[str, Any]:
    revs = control.revisions
    latest = revs[-1] if revs else None
    approved = any(r.status == RevisionStatus.APPROVED for r in revs)
    plans = list(ctx.session.scalars(select(m.RolloutPlan).where(m.RolloutPlan.control_id == control.id)))
    observed = any(STAGE_ORDER.index(RolloutStage(p.stage)) >= STAGE_ORDER.index(RolloutStage.OBSERVATION)
                   for p in plans)
    verified = ctx.session.scalars(select(m.DeliveryTarget).where(
        m.DeliveryTarget.control_id == control.id, m.DeliveryTarget.state == DeliveryState.VERIFIED)).first()
    return {"latest_status": latest.status if latest else None,
            "proposed": bool(latest and latest.status in (RevisionStatus.DRAFT, RevisionStatus.IN_REVIEW)),
            "approved": approved, "observed": observed, "verified": verified is not None,
            "target_scopes": sorted({p.target_scope_id for p in plans})}


def metrics(ctx: RequestContext) -> dict[str, Any]:
    tree = ctx.tree
    readable = ctx.principal.readable_scope_ids(tree)
    warn_days = settings.get(ctx.session, "expiry")["warning_days"]
    controls = list(ctx.session.scalars(select(m.Control).order_by(m.Control.id)))
    per_control = []
    pair_totals = Counter()
    lifecycle_by_scope: dict[str, Counter] = {}
    gap_counts = Counter()
    blocker_kinds = Counter()
    for c in controls:
        lc = control_lifecycle(ctx, c)
        for scope in lc["target_scopes"] or ["(no rollout plan)"]:
            bucket = lifecycle_by_scope.setdefault(scope, Counter())
            for k in ("proposed", "approved", "observed", "verified"):
                bucket[k] += int(lc[k])
        run = latest_run_for_control(ctx, c.id)
        cov = resource_coverage(ctx, c.id, run) if run else None
        if run:
            for b in run.blockers:
                if readable is None or b.get("scope_id") in readable:
                    blocker_kinds[b["kind"]] += 1
        if cov:
            gap_counts.update(cov["baseline_counts"])
            for k in ("known_applicable_resources", "verified_protected_resources", "effective_exemptions",
                      "unknown_enforcement", "unknown_applicability", "remaining_noncompliant",
                      "unknown_configuration", "missing_owner", "not_enforced"):
                pair_totals[k] += cov[k]
        latest = c.revisions[-1] if c.revisions else None
        per_control.append({
            "control_id": c.id, "name": latest.name if latest else c.id, "origin": c.origin,
            "operational_owner": c.operational_owner, "lifecycle": lc,
            "coverage": {k: v for k, v in (cov or {}).items() if k != "per_resource"} if cov else None,
            "links": {"control": f"/controls/{c.id}",
                      "assessment": f"/assessments/{run.id}" if run else None},
        })
    exc_rows = list(ctx.session.scalars(select(m.SecurityException)))
    if readable is not None:
        exc_rows = [e for e in exc_rows if e.scope_id in readable]
    eff = Counter(effective_status(e, ctx.now) for e in exc_rows)
    disp = Counter(disposition(e, ctx.now) for e in exc_rows)
    expiring = [e.id for e in exc_rows if effective_status(e, ctx.now) == "APPROVED"
                and timedelta(0) <= e.expires_at - ctx.now <= timedelta(days=warn_days)]
    cleanup = [e.id for e in exc_rows if e.native_status == "REMOVAL_PENDING" or (
        effective_status(e, ctx.now) in ("EXPIRED", "REVOKED") and e.native_status == "APPLIED")]
    packages = list(ctx.session.scalars(select(m.ChangePackage).where(
        m.ChangePackage.status == PackageStatus.IN_REVIEW)))
    awaiting_sec, awaiting_eng, gated = [], [], []
    for p in packages:
        decisions = list(ctx.session.scalars(select(m.ApprovalDecision).where(
            m.ApprovalDecision.subject_id == p.id, m.ApprovalDecision.subject_digest == p.manifest_digest)))
        roles = {d.role for d in decisions if d.decision == "APPROVE"}
        if any(g["status"] != "PASS" and g["phase"] == "PRE_APPROVAL" for g in p.gate_results):
            gated.append(p.id)
        if "SECURITY_APPROVER" not in roles:
            awaiting_sec.append(p.id)
        if "CLOUD_ENGINEER" not in roles:
            awaiting_eng.append(p.id)
    targets = list(ctx.session.scalars(select(m.DeliveryTarget)))
    if readable is not None:
        targets = [t for t in targets if t.scope_id in readable]
    delivery = Counter(t.state for t in targets)
    open_work = list(ctx.session.scalars(select(m.WorkReference).where(m.WorkReference.status == "OPEN")))
    if readable is not None:
        open_work = [w for w in open_work if w.scope_id in readable]
    work_kinds = Counter(w.kind for w in open_work)
    stale_evidence = 0
    max_age = timedelta(days=settings.get(ctx.session, "evidence_freshness")["readiness_max_age_days"])
    for e in ctx.session.scalars(select(m.ReadinessEvidence)):
        if (readable is None or e.scope_id in readable) and ctx.now - e.collected_at > max_age:
            stale_evidence += 1
    return {
        "generated_at": iso(ctx.now),
        "scope_filter": "all scopes" if readable is None else f"{len(readable)} authorised scopes",
        "controls": {
            "total_catalogued": len(controls),
            "note": "Catalogue size is not a measure of effectiveness.",
            "lifecycle_by_target_scope": {k: dict(v) for k, v in sorted(lifecycle_by_scope.items())},
            "per_control": per_control,
        },
        "coverage_pairs": {
            "unit": "control-resource pairs (a resource assessed by two controls counts twice)",
            **dict(pair_totals),
            "verified_protected_ratio": ratio(pair_totals["verified_protected_resources"],
                                              pair_totals["known_applicable_resources"]),
            "denominator": "Sum over controls of known-applicable resources in each control's latest completed "
                           "assessment. Exceptions are not removed from the denominator.",
        },
        "gaps_vs_existing_native_coverage": {
            "baseline_counts": dict(gap_counts),
            "definition": "Applicable control-resource pairs by existing native coverage before the proposed change "
                          "(NONE/AUDIT_ONLY/PARTIAL = gap for prevention).",
        },
        "exceptions": {
            "by_effective_status": dict(eff), "by_disposition": dict(disp),
            "expiring_within_days": {"days": warn_days, "ids": expiring},
            "expired": eff.get("EXPIRED", 0),
            "pending_native_cleanup": {"ids": cleanup,
                                       "open_work": work_kinds.get("EXEMPTION_REMOVAL", 0)
                                       + work_kinds.get("EXEMPTION_CLEANUP", 0)},
            "links": {"list": "/exceptions"},
        },
        "evidence": {
            "unknown_configuration_pairs": pair_totals["unknown_configuration"],
            "stale_readiness_evidence_items": stale_evidence,
            "missing_owner_pairs": pair_totals["missing_owner"],
        },
        "bottlenecks": {
            "packages_awaiting_security_approval": awaiting_sec,
            "packages_awaiting_engineering_approval": awaiting_eng,
            "packages_with_failing_pre_approval_gates": gated,
            "readiness_blockers_by_kind": dict(blocker_kinds),
            "links": {"rollouts": "/rollouts"},
        },
        "delivery": {
            "targets_by_state": dict(delivery),
            "drifted_targets": delivery.get("DRIFTED", 0),
            "open_drift_work": work_kinds.get("DRIFT", 0),
            "provenance": "All receipts and observations are MOCK/FIXTURE data in this MVP.",
        },
        "open_work_by_kind": dict(work_kinds),
        "outcome_metrics": [
            {"id": "recurrence_rate", "status": "UNAVAILABLE",
             "reason": "Needs trustworthy finding history (e.g. Wiz) over time; not integrated."},
            {"id": "time_to_verified_coverage", "status": "UNAVAILABLE",
             "reason": "Needs live observations; fixture observations cannot support it."},
            {"id": "policy_induced_incidents", "status": "UNAVAILABLE",
             "reason": "Needs incident system integration."},
            {"id": "denied_operations", "status": "UNAVAILABLE",
             "reason": "Needs trustworthy deny telemetry (activity logs / CloudTrail); not ingested. Posture findings "
                       "are never used to infer runtime events or prevented attacks."},
        ],
    }
