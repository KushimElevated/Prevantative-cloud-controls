"""GitOps handoff: deterministic bundle export, mock pipeline receipts and observations.

Exported or approved is not deployed. A pipeline success is not verification. Only a matching,
fresh observation after a valid APPLIED receipt marks a delivery target VERIFIED, and all
receipts/observations here are clearly labelled mock/fixture data.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.api.schemas import ReceiptPayload
from app.auth.principal import require_any_role, require_global_role
from app.core.clock import iso
from app.core.digests import digest, pretty_json
from app.core.errors import Conflict, GateFailed, NotFound, Unsupported, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import (
    BundleKind,
    ComparisonResult,
    DeliveryState,
    NativeStatus,
    PackageStatus,
    PlanState,
    ReceiptValidation,
    Role,
)
from app.providers.common import short_hash
from app.providers.handoff import get_handoff_adapter
from app.providers.registry import get_provider
from app.services import audit, settings
from app.services.context import RequestContext
from app.services.exceptions import ensure_work
from app.services.views import receipt_view
from app.services.rollouts import (
    delivered_marker,
    evaluate_gates,
    failing,
    get_package,
    get_plan,
    mark_stale,
    package_decisions,
    stage_index,
)

BUNDLE_SCHEMA = "ccp.handoff-bundle/v1"


# ------------------------------------------------------------------------------------ bundle


def _artifacts(ctx: RequestContext, pkg: m.ChangePackage):
    plan = ctx.session.get(m.RolloutPlan, pkg.plan_id)
    provider = get_provider(plan.provider)
    irev = ctx.session.get(m.ImplementationRevision, pkg.manifest["implementations"][0]["revision_id"])
    return plan, provider, irev, provider.generate_change_artifacts(pkg.manifest, irev, ctx.tree)


def _receipt_schema() -> dict[str, Any]:
    schema = ReceiptPayload.model_json_schema()
    schema["title"] = "CCP deployment receipt (v1)"
    schema["description"] = ("One receipt per target scope and ring. provenance must be MOCK in this MVP; "
                             "authenticated LIVE callbacks are a deferred increment.")
    return schema


def build_bundle_files(ctx: RequestContext, pkg: m.ChangePackage, approved: bool) -> dict[str, str]:
    plan, provider, irev, artifacts = _artifacts(ctx, pkg)
    man = pkg.manifest
    impl = man["implementations"][0]
    demo_only = not impl["integration_ready"]
    if approved:
        banner = "APPROVED FOR HANDOFF - not deployed. Cloud Engineering executes delivery."
        if demo_only:
            banner = "APPROVED BUT DEMO-ONLY - implementation not verified against primary documentation."
    else:
        banner = "DRAFT - UNAPPROVED - NOT FOR DEPLOYMENT."
    tree = ctx.tree
    run_id = man["assessment_refs"][0]["run_id"] if man["assessment_refs"] else None
    run = ctx.session.get(m.AssessmentRun, run_id) if run_id else None
    validation = ctx.session.get(m.ImplementationValidation, impl["validation_id"]) if impl["validation_id"] else None
    decisions = [d for d in package_decisions(ctx, pkg) if d.subject_digest == pkg.manifest_digest]

    files: dict[str, str] = {}
    for a in artifacts:
        files[a.path] = pretty_json(a.content)
    intended = [{"ring": a.ring_stage, "kind": a.kind, "native_identity": a.native_identity, "scope_id": a.scope_id,
                 "enforcing": a.enforcing, "exception_id": a.exception_id, "file": a.path,
                 "config_digest": digest(a.content), "notes": a.notes} for a in artifacts]
    files["parameters.json"] = pretty_json({
        "implementation": {k: impl[k] for k in ("id", "revision_id", "policy_kind", "source_ref", "pinned_version",
                                                "content_digest", "parameters", "assignment_settings")},
        "ring_settings": [{"stage": r["stage"], "settings": r["settings"], "enforcing": r["enforcing"]}
                          for r in man["deploy_rings"]],
        "intended_changes": intended,
    })
    files["scopes.json"] = pretty_json({
        "target": {"id": man["plan"]["target_scope_id"], "native_id": man["plan"]["target_scope_native_id"],
                   "path": tree.path(man["plan"]["target_scope_id"])},
        "rings": [{"stage": r["stage"], "scopes": [
            {"id": s, "native_id": tree.get(s).native_id, "scope_type": tree.get(s).scope_type,
             "resolved_descendants": [{"id": d, "native_id": tree.get(d).native_id}
                                      for d in r["resolved_descendants"][s]]}
            for s in r["scope_ids"]]} for r in man["deploy_rings"]],
    })
    files["baseline-comparison.json"] = pretty_json({
        "existing_bindings": man["baseline"],
        "assessment_baseline_vs_proposed": run.rollup.get("baseline_vs_proposed") if run else None,
        "note": "Existing bindings are recorded with desired/observed digests. Possible overlaps are flagged by "
                "native id, digest and scope only; semantic equivalence is not claimed.",
    })
    files["assessment-summary.json"] = pretty_json({
        "assessment_run_id": run_id,
        "disclosure": run.disclosure if run else "No assessment.",
        "rollup": run.rollup if run else None,
        "confidence": run.confidence if run else None,
        "blockers": run.blockers if run else [],
        "limitations": run.limitations if run else [],
        "assumptions": run.assumptions if run else [],
        "validation": {"outcome": validation.outcome, "checks": validation.checks,
                       "tests": validation.test_results, "integration_ready": validation.integration_ready,
                       "integration_blockers": validation.integration_blockers} if validation else None,
        "prerequisites": {"provider": provider.readiness_prerequisites(),
                          "plan_prerequisite_changes": man["prerequisite_changes"]},
    })
    files["exceptions.json"] = pretty_json({
        **man["exception_set"],
        "exemption_artifacts": [i for i in intended if i["exception_id"]],
        "native_expiry_supported": man["provider_capabilities"]["native_expiry_supported"],
    })
    files["approvals/attestations.json"] = pretty_json({
        "package_id": pkg.id, "package_revision": pkg.package_revision, "manifest_digest": pkg.manifest_digest,
        "status": "APPROVED" if approved else "UNAPPROVED",
        "attestations": [{"decision_id": d.id, "actor_id": d.actor_id, "role": d.role, "decision": d.decision,
                          "rationale": d.rationale, "decided_at": iso(d.decided_at),
                          "subject_digest": d.subject_digest} for d in decisions],
        "note": "Attestations are stored separately from the manifest to avoid circular hashing.",
    })
    files["REVIEWERS.md"] = "\n".join([
        f"# Reviewer instructions - {man['control']['id']} r{man['control']['revision']}", "",
        f"Status: **{banner}**", "",
        "1. Confirm `manifest.json` `change_manifest_digest` equals the digest in `approvals/attestations.json`.",
        "2. Review every file under `native/` against `parameters.json` (intended changes and config digests).",
        "3. Review `exceptions.json`: additions, renewals, removals and expiry consequences.",
        "4. Review open blockers and unknowns in `assessment-summary.json`.",
        "5. Confirm `ROLLBACK.md` is executable by Cloud Engineering for every ring.",
        "6. Report one deployment receipt per target scope using `receipt-schema.json`.", ""])
    files["rollout.json"] = pretty_json({
        "through_stage": man["plan"]["through_stage"],
        "rings": [{
            "stage": r["stage"], "scope_ids": r["scope_ids"], "settings": r["settings"],
            "enforcing": r["enforcing"], "artifact_required": r["artifact_required"],
            "entry_gates": ["Platform rollout stage advanced to this ring by Cloud Engineering",
                            "Previous native rings VERIFIED by fresh matching observations",
                            "No open readiness blockers in this ring",
                            "Package not stale at acceptance (approvals, exceptions, evidence, baseline rechecked)"],
        } for r in man["deploy_rings"]],
        "all_planned_rings": man["plan"]["all_rings"],
        "pause_criteria": man["pause_criteria"],
        "ordering": "Within a ring, apply exemptions before enforcing assignments/attachments.",
    })
    rb = man["rollback_plan"]
    files["ROLLBACK.md"] = "\n".join(
        [f"# Rollback and recovery - {man['control']['id']}", "",
         "Rollback means a reviewed change back to a prior known-good policy/binding where feasible. It does not undo "
         "application changes and does not guarantee immediate recovery.", "",
         f"Prior known-good: {rb['prior_known_good']}", "", "## Steps"]
        + [f"{i}. {s}" for i, s in enumerate(rb["steps"], 1)]
        + ["", "## Limitations"] + [f"- {s}" for s in rb["limitations"]]
        + (["- SCP note: the SCP blocks all non-exempt principals from changing Block Public Access settings; "
            "rollback and any baseline correction must use an exempt administrative role."]
           if plan.provider == "aws" else [])
        + ["", "## Emergency recovery path (Cloud Engineering)", rb["emergency_path"],
           "", "This platform provides no bypass role and performs no cloud mutations.", ""])
    files["PR_DESCRIPTION.md"] = "\n".join([
        f"## {man['control']['name']}", "",
        f"**{banner}**", "",
        f"- Control: `{man['control']['id']}` revision {man['control']['revision']} "
        f"(`{man['control']['content_digest']}`)",
        f"- Implementation: `{impl['id']}` r{impl['revision']} {impl['policy_kind']} `{impl['source_ref']}` "
        f"pinned {impl['pinned_version']}",
        f"- Change manifest digest: `{pkg.manifest_digest}`",
        f"- Rings in this change: {', '.join(r['stage'] for r in man['deploy_rings'])}",
        f"- Exception additions: {len(man['exception_set']['additions'])}, removals: "
        f"{len(man['exception_set']['removals'])}", "",
        "### Prevention boundary", man["control"]["prevention_boundary"], "",
        "### What this does not do",
        "- It does not remediate existing non-compliant resources.",
        "- It does not prove end-to-end authorization or application availability.",
        "- Merging/applying is executed by Cloud Engineering's pipeline, not by this platform.", ""])
    files["receipt-schema.json"] = pretty_json(_receipt_schema())
    files["README.md"] = "\n".join([
        f"# Handoff bundle - {man['control']['id']}", "", f"**{banner}**", "",
        "Generated deterministically from an immutable change package. Identical packages produce identical bytes.",
        "See `manifest.json` for content digests of every file.", ""])
    file_digests = {path: digest(content) for path, content in sorted(files.items())}
    files["manifest.json"] = pretty_json({
        "schema": BUNDLE_SCHEMA, "bundle_kind": BundleKind.APPROVED_HANDOFF if approved else BundleKind.DRAFT_UNAPPROVED,
        "status_banner": banner, "demo_only": demo_only,
        "package": {"id": pkg.id, "revision": pkg.package_revision, "plan_id": pkg.plan_id},
        "change_manifest_digest": pkg.manifest_digest, "change_manifest": man, "file_digests": file_digests,
    })
    return files


def bundle_digest(files: dict[str, str]) -> str:
    return digest(sorted((path, digest(content)) for path, content in files.items()))


def export_bundle(ctx: RequestContext, package_id: str, approved: bool) -> m.HandoffBundle:
    require_any_role(ctx.principal, [Role.CLOUD_ENGINEER, Role.CONTROL_ENGINEER], action="Exporting a bundle")
    pkg = get_package(ctx, package_id, lock=True)
    plan = get_plan(ctx, pkg.plan_id)
    checks: list[dict] = []
    if approved:
        if pkg.status != PackageStatus.APPROVED:
            raise Conflict(f"Only APPROVED packages can be exported for handoff (package is {pkg.status}). "
                           "Use the draft export for review copies.")
        gates = evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=True)
        if failing(gates):
            mark_stale(ctx, pkg, gates, "export")
            ctx.commit()
            raise GateFailed("Export refused: package is stale.",
                             details=[{"id": g["id"], "detail": g["detail"]} for g in failing(gates)])
        checks = gates
    files = build_bundle_files(ctx, pkg, approved)
    bdigest = bundle_digest(files)
    kind = BundleKind.APPROVED_HANDOFF if approved else BundleKind.DRAFT_UNAPPROVED
    existing = ctx.session.scalars(select(m.HandoffBundle).where(
        m.HandoffBundle.package_id == pkg.id, m.HandoffBundle.kind == kind,
        m.HandoffBundle.bundle_digest == bdigest)).first()
    if existing:
        return existing
    adapter = get_handoff_adapter(ctx.settings.handoff_adapter, ctx.settings.export_dir)
    path = adapter.export(pkg.id, bdigest, files)
    bundle = m.HandoffBundle(id=new_id("bndl"), package_id=pkg.id, kind=kind, bundle_digest=bdigest, files=files,
                             file_digests={p: digest(c) for p, c in sorted(files.items())}, adapter=adapter.name,
                             export_path=path, export_checks=checks, exported_at=ctx.now,
                             exported_by=ctx.principal.user_id)
    ctx.session.add(bundle)
    ctx.session.flush()
    if approved:
        _, _, irev, artifacts = _artifacts(ctx, pkg)
        for a in artifacts:
            ctx.session.add(m.DeliveryTarget(
                id=new_id("dtgt"), bundle_id=bundle.id, plan_id=plan.id, control_id=plan.control_id,
                ring_stage=a.ring_stage, scope_id=a.scope_id, artifact_kind=a.kind,
                native_identity=a.native_identity, expected_state=a.content, expected_digest=digest(a.content),
                enforcing=a.enforcing, exception_id=a.exception_id, binding_id=None,
                state=DeliveryState.EXPORTED, state_changed_at=ctx.now, state_basis_at=None,
                provenance="MOCK_PIPELINE"))
        ctx.session.flush()
    audit.record(ctx, action="handoff.exported" if approved else "handoff.draft_exported",
                 object_type="handoff_bundle", object_id=bundle.id, control_id=plan.control_id,
                 after_digest=bdigest, scope_ids=[plan.target_scope_id],
                 details={"package_id": pkg.id, "kind": kind, "export_path": path})
    return bundle


def get_bundle(ctx: RequestContext, bundle_id: str) -> m.HandoffBundle:
    b = ctx.session.get(m.HandoffBundle, bundle_id)
    if b is None:
        raise NotFound(f"Bundle {bundle_id} not found")
    get_package(ctx, b.package_id)
    return b


# ------------------------------------------------------------------------------------ receipts


def ingest_receipt(ctx: RequestContext, payload: ReceiptPayload) -> tuple[m.DeploymentReceipt, bool]:
    require_global_role(ctx.principal, Role.CLOUD_ENGINEER, action="Recording a deployment receipt")
    if payload.provenance != "MOCK":
        raise Unsupported("LIVE receipts are not accepted: authenticated pipeline callbacks are a deferred "
                          "increment. Only clearly labelled MOCK receipts are processed.")
    raw = payload.model_dump(mode="json")
    pdigest = digest(raw)
    existing = ctx.session.scalars(select(m.DeploymentReceipt).where(
        m.DeploymentReceipt.receipt_key == payload.receipt_id)).first()
    if existing is not None:
        if existing.payload_digest == pdigest:
            return existing, True
        raise Conflict("A different receipt with this receipt_id was already recorded.", code="RECEIPT_CONFLICT")
    notes: list[str] = []
    status = ReceiptValidation.VALID
    targets: list[m.DeliveryTarget] = []
    bundle = ctx.session.scalars(select(m.HandoffBundle).where(
        m.HandoffBundle.bundle_digest == payload.bundle_digest)).first()
    fresh = settings.get(ctx.session, "evidence_freshness")
    if bundle is None:
        status, notes = ReceiptValidation.REJECTED_DIGEST_MISMATCH, ["Unknown bundle digest."]
    elif bundle.kind != BundleKind.APPROVED_HANDOFF:
        status, notes = ReceiptValidation.REJECTED_NOT_AUTHORIZED, ["Draft (unapproved) bundles are not deployable."]
    else:
        pkg = ctx.session.get(m.ChangePackage, bundle.package_id)
        plan = ctx.session.get(m.RolloutPlan, pkg.plan_id)
        targets = [t for t in ctx.session.scalars(select(m.DeliveryTarget).where(
            m.DeliveryTarget.bundle_id == bundle.id, m.DeliveryTarget.ring_stage == payload.ring))
            if ctx.tree.get(t.scope_id).native_id == payload.target_scope_native_id]
        if not targets:
            status, notes = ReceiptValidation.REJECTED_SCOPE_MISMATCH, [
                f"No {payload.ring} delivery target at {payload.target_scope_native_id} in this bundle."]
        elif plan.state == PlanState.CANCELLED:
            status, notes = ReceiptValidation.REJECTED_NOT_AUTHORIZED, ["Rollout plan is cancelled."]
        elif stage_index(payload.ring) > stage_index(plan.stage):
            status, notes = ReceiptValidation.REJECTED_NOT_AUTHORIZED, [
                f"Ring {payload.ring} is not yet authorized (plan stage {plan.stage})."]
        elif payload.result == "APPLIED":
            reported = {i.native_identity: i.config_digest for i in payload.applied_native_identities}
            mismatched = [t.native_identity for t in targets if reported.get(t.native_identity) != t.expected_digest]
            if mismatched:
                status = ReceiptValidation.REJECTED_DIGEST_MISMATCH
                notes = [f"Reported configuration digest differs from the approved artifact for {x}."
                         for x in mismatched]
        if status == ReceiptValidation.VALID:
            if ctx.now - payload.completed_at > timedelta(hours=fresh["receipt_max_age_hours"]):
                status, notes = ReceiptValidation.STALE, ["Receipt completed outside the freshness window."]
            elif payload.result in ("ACCEPTED", "APPLIED"):
                gates = evaluate_gates(ctx, plan, pkg.manifest, pkg, include_approvals=True)
                if pkg.status != PackageStatus.APPROVED or failing(gates):
                    if pkg.status == PackageStatus.APPROVED:
                        mark_stale(ctx, pkg, gates, "receipt acceptance")
                    status = ReceiptValidation.STALE
                    notes = [f"Package is {pkg.status}; acceptance recheck failed: "
                             + ", ".join(g["id"] for g in failing(gates))]
        if status == ReceiptValidation.VALID:
            ooo = [t for t in targets if t.state_basis_at and payload.completed_at < t.state_basis_at]
            if ooo:
                status = ReceiptValidation.OUT_OF_ORDER
                notes = ["Older than the latest state for this target; history kept, state not replaced."]
    receipt = m.DeploymentReceipt(
        id=new_id("rcpt"), receipt_key=payload.receipt_id, payload_digest=pdigest, payload=raw,
        bundle_id=bundle.id if bundle else None, bundle_digest=payload.bundle_digest, ring_stage=payload.ring,
        target_scope_native_id=payload.target_scope_native_id, pipeline_run_ref=payload.pipeline_run_ref,
        applied_native_identities=[i.model_dump() for i in payload.applied_native_identities],
        result=payload.result, reported_completed_at=payload.completed_at, received_at=ctx.now,
        provenance="MOCK", validation_status=status, validation_notes=notes,
        delivery_target_ids=[t.id for t in targets])
    ctx.session.add(receipt)
    ctx.session.flush()
    if status == ReceiptValidation.VALID:
        new_state = {"ACCEPTED": DeliveryState.ACCEPTED, "APPLIED": DeliveryState.APPLIED,
                     "FAILED": DeliveryState.FAILED}[payload.result]
        for t in targets:
            if payload.result == "ACCEPTED" and t.state != DeliveryState.EXPORTED:
                continue
            t.state = new_state
            t.state_changed_at = ctx.now
            t.state_basis_at = payload.completed_at
            t.last_receipt_id = receipt.id
            if payload.result == "FAILED":
                ensure_work(ctx.session, ctx.now, kind="DELIVERY_FAILURE", owner_team="Cloud Engineering",
                            title=f"Delivery failed for {t.native_identity}",
                            detail=payload.error or "Pipeline reported FAILED (mock).", related_type="delivery_target",
                            related_id=t.id, scope_id=t.scope_id, dedupe_key=f"delivery-failure:{t.id}:{receipt.id}")
            if t.exception_id and payload.result in ("APPLIED", "FAILED"):
                exc = ctx.session.get(m.SecurityException, t.exception_id)
                exc.native_status = NativeStatus.APPLIED if payload.result == "APPLIED" else NativeStatus.FAILED
                exc.native_provenance = "MOCK_PIPELINE"
                exc.updated_at = ctx.now
    elif status in (ReceiptValidation.REJECTED_DIGEST_MISMATCH, ReceiptValidation.REJECTED_SCOPE_MISMATCH):
        ensure_work(ctx.session, ctx.now, kind="DELIVERY_FAILURE", owner_team="Cloud Engineering",
                    title=f"Mismatched deployment receipt {payload.receipt_id}", detail="; ".join(notes),
                    related_type="deployment_receipt", related_id=receipt.id, scope_id=None,
                    dedupe_key=f"receipt-mismatch:{receipt.id}")
    ctx.session.flush()
    audit.record(ctx, action="delivery.receipt_recorded", object_type="deployment_receipt", object_id=receipt.id,
                 after_digest=pdigest, control_id=targets[0].control_id if targets else None,
                 scope_ids=[t.scope_id for t in targets],
                 details={"validation_status": status, "result": payload.result, "provenance": "MOCK",
                          "notes": notes})
    return receipt, False


def run_mock_pipeline(ctx: RequestContext, bundle_id: str, ring: str, scenario: str) -> list[dict[str, Any]]:
    """Generates clearly labelled MOCK receipts and processes them through the same validation path."""
    require_global_role(ctx.principal, Role.CLOUD_ENGINEER, action="Running the mock pipeline")
    bundle = get_bundle(ctx, bundle_id)
    targets = list(ctx.session.scalars(select(m.DeliveryTarget).where(
        m.DeliveryTarget.bundle_id == bundle.id, m.DeliveryTarget.ring_stage == ring)
        .order_by(m.DeliveryTarget.native_identity)))
    if not targets and bundle.kind == BundleKind.APPROVED_HANDOFF:
        raise ValidationFailed(f"Bundle has no {ring} delivery targets.")
    by_scope: dict[str, list[m.DeliveryTarget]] = {}
    for t in targets:
        by_scope.setdefault(ctx.tree.get(t.scope_id).native_id, []).append(t)
    if bundle.kind != BundleKind.APPROVED_HANDOFF:
        by_scope = {"(draft bundle)": []}
    count = len(list(ctx.session.scalars(select(m.DeploymentReceipt.id).where(
        m.DeploymentReceipt.bundle_id == bundle.id))))
    results = []
    for i, (scope_native, ts) in enumerate(sorted(by_scope.items())):
        completed: datetime = ctx.now
        if scenario == "STALE":
            hours = settings.get(ctx.session, "evidence_freshness")["receipt_max_age_hours"]
            completed = ctx.now - timedelta(hours=hours + 1)
        identities = []
        for t in ts:
            d = t.expected_digest if scenario != "MISMATCH" else digest({"tampered": t.expected_digest})
            identities.append({"native_identity": t.native_identity, "config_digest": d})
        payload = ReceiptPayload(
            receipt_id=f"mock-{short_hash(bundle.id, ring, scope_native, scenario, count + i, length=20)}",
            provenance="MOCK", bundle_digest=bundle.bundle_digest, ring=ring, target_scope_native_id=scope_native,
            pipeline_run_ref=f"MOCK-RUN-{short_hash(bundle.id, ring, count + i, length=10)}",
            result="FAILED" if scenario == "FAILURE" else "APPLIED",
            applied_native_identities=[] if scenario == "FAILURE" else identities,
            started_at=completed - timedelta(minutes=5), completed_at=completed,
            error="Mock pipeline failure (simulated)." if scenario == "FAILURE" else None)
        receipt, dup = ingest_receipt(ctx, payload)
        results.append({**receipt_view(receipt), "duplicate": dup, "payload": payload.model_dump(mode="json")})
    return results


# ------------------------------------------------------------------------------------ observations


def record_mock_observation(ctx: RequestContext, target_id: str, scenario: str,
                            observed_at: datetime | None) -> m.Observation:
    require_global_role(ctx.principal, Role.CLOUD_ENGINEER, action="Recording an observation")
    t = ctx.session.get(m.DeliveryTarget, target_id, with_for_update=True)
    if t is None:
        raise NotFound(f"Delivery target {target_id} not found")
    plan = ctx.session.get(m.RolloutPlan, t.plan_id)
    provider = get_provider(plan.provider)
    when = observed_at or ctx.now
    if when > ctx.now:
        raise ValidationFailed("Observation time cannot be in the future.")
    observed = provider.mock_observed_state(t.expected_state, scenario, t.artifact_kind)
    cmp = provider.compare_observed_state(t.expected_state, observed, t.artifact_kind)
    fresh = settings.get(ctx.session, "evidence_freshness")
    obs_max = timedelta(hours=fresh["observation_max_age_hours"])
    last = ctx.session.get(m.Observation, t.last_observation_id) if t.last_observation_id else None
    notes: list[str] = ["MOCK observation generated from fixture scenario " + scenario]
    applied = True
    if last is not None and when < last.observed_at:
        applied = False
        notes.append("Older than the latest observation; recorded without replacing newer state.")
    obs = m.Observation(id=new_id("obs"), delivery_target_id=t.id, binding_id=None, exception_id=t.exception_id,
                        native_identity=t.native_identity, observed_state=observed,
                        observed_digest=digest(observed) if observed is not None else None, observed_at=when,
                        recorded_at=ctx.now, source="mock-observer", provenance="FIXTURE",
                        comparison=cmp.result, differences=cmp.differences, applied_to_state=applied, notes=notes)
    ctx.session.add(obs)
    ctx.session.flush()
    if applied:
        receipt = ctx.session.get(m.DeploymentReceipt, t.last_receipt_id) if t.last_receipt_id else None
        if cmp.result == ComparisonResult.MATCH:
            if t.state not in (DeliveryState.APPLIED, DeliveryState.VERIFIED, DeliveryState.DRIFTED) or receipt is None:
                notes.append(f"Matches, but target is {t.state} without a valid APPLIED receipt; not verified.")
            elif when < receipt.reported_completed_at:
                notes.append("Observation predates the applied receipt; not verified.")
            elif ctx.now - when > obs_max:
                notes.append("Observation is stale; not verified.")
            else:
                t.state = DeliveryState.VERIFIED
                t.verified_at = when
                t.state_changed_at = ctx.now
        else:
            if t.state in (DeliveryState.APPLIED, DeliveryState.VERIFIED):
                t.state = DeliveryState.DRIFTED
                t.state_changed_at = ctx.now
                ensure_work(ctx.session, ctx.now, kind="DRIFT", owner_team="Cloud Engineering",
                            title=f"Drift on {t.native_identity}",
                            detail="Observed native state differs from the approved artifact: "
                                   + "; ".join(cmp.differences) + ". No automatic correction is performed.",
                            related_type="delivery_target", related_id=t.id, scope_id=t.scope_id,
                            dedupe_key=f"drift:{t.id}:{obs.id}")
        t.last_observation_id = obs.id
        obs.notes = notes
        _sync_binding_and_exception(ctx, t, plan, observed, when, cmp.result)
    ctx.session.flush()
    audit.record(ctx, action="delivery.observation_recorded", object_type="delivery_target", object_id=t.id,
                 control_id=t.control_id, scope_ids=[t.scope_id], after_digest=obs.observed_digest,
                 details={"comparison": cmp.result, "state": t.state, "provenance": "FIXTURE",
                          "applied_to_state": applied})
    return obs


def _sync_binding_and_exception(ctx: RequestContext, t: m.DeliveryTarget, plan: m.RolloutPlan,
                                observed: dict | None, when: datetime, comparison: str) -> None:
    if t.exception_id:
        exc = ctx.session.get(m.SecurityException, t.exception_id)
        exc.native_observed = observed
        exc.native_observed_at = when
        exc.native_provenance = "FIXTURE"
        if comparison == ComparisonResult.MATCH and t.state == DeliveryState.VERIFIED:
            exc.native_status = NativeStatus.APPLIED
        elif comparison == ComparisonResult.MISSING:
            exc.native_status = NativeStatus.UNKNOWN
        exc.updated_at = ctx.now
        return
    pkg_impl = ctx.session.get(m.HandoffBundle, t.bundle_id)
    pkg = ctx.session.get(m.ChangePackage, pkg_impl.package_id)
    impl = pkg.manifest["implementations"][0]
    b = ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.native_id == t.native_identity)).first()
    if b is None:
        b = m.PolicyBinding(
            id=new_id("bind"), control_id=t.control_id, implementation_revision_id=impl["revision_id"],
            provider=plan.provider,
            binding_kind="AZURE_POLICY_ASSIGNMENT" if plan.provider == "azure" else "AWS_SCP_ATTACHMENT",
            native_id=t.native_identity, definition_ref=impl["source_ref"], definition_digest=impl["content_digest"],
            target_scope_id=t.scope_id,
            settings=next(r["settings"] for r in pkg.manifest["deploy_rings"] if r["stage"] == t.ring_stage),
            exclusions=(impl["assignment_settings"] or {}).get("notScopes", []),
            source_repository=delivered_marker(plan.id), management="CLOUD_ENGINEERING_PIPELINE",
            origin="DELIVERED_FROM_PACKAGE", desired_state=t.expected_state, observed_state=None, observed_at=None,
            evidence_provenance="MOCK_PIPELINE", created_at=ctx.now)
        ctx.session.add(b)
        ctx.session.flush()
    b.desired_state = t.expected_state
    b.observed_state = observed
    b.observed_at = when
    t.binding_id = b.id


def record_binding_observation(ctx: RequestContext, binding_id: str, scenario: str) -> m.Observation:
    """Fixture observation of an existing (e.g. externally managed) binding. Clearly labelled FIXTURE."""
    require_global_role(ctx.principal, Role.CLOUD_ENGINEER, action="Recording an observation")
    b = ctx.session.get(m.PolicyBinding, binding_id, with_for_update=True)
    if b is None:
        raise NotFound(f"Binding {binding_id} not found")
    provider = get_provider(b.provider)
    kind = "AZURE_POLICY_ASSIGNMENT" if b.provider == "azure" else "AWS_SCP_ATTACHMENT"
    observed = provider.mock_observed_state(b.desired_state, scenario, kind)
    c = provider.compare_observed_state(b.desired_state, observed, kind)
    cmp_result, diffs = c.result, c.differences
    obs = m.Observation(id=new_id("obs"), delivery_target_id=None, binding_id=b.id, exception_id=None,
                        native_identity=b.native_id, observed_state=observed,
                        observed_digest=digest(observed) if observed is not None else None, observed_at=ctx.now,
                        recorded_at=ctx.now, source="mock-observer", provenance="FIXTURE", comparison=cmp_result,
                        differences=diffs, applied_to_state=True,
                        notes=[f"FIXTURE observation of existing binding (scenario {scenario})"])
    ctx.session.add(obs)
    b.observed_state = observed
    b.observed_at = ctx.now
    b.evidence_provenance = "FIXTURE"
    ctx.session.flush()
    audit.record(ctx, action="binding.observation_recorded", object_type="policy_binding", object_id=b.id,
                 control_id=b.control_id, scope_ids=[b.target_scope_id], after_digest=obs.observed_digest,
                 details={"comparison": cmp_result, "provenance": "FIXTURE"})
    return obs
