"""Entity-to-JSON views used by the API. Timestamps are ISO-8601 UTC."""

from __future__ import annotations

from typing import Any

from app.core.clock import iso
from app.models import entities as m


def control_revision_view(r: m.ControlRevision) -> dict[str, Any]:
    return {
        "id": r.id, "control_id": r.control_id, "revision": r.revision, "status": r.status,
        "name": r.name, "description": r.description, "security_objective": r.security_objective,
        "rationale": r.rationale, "source_evidence": r.source_evidence, "severity": r.severity,
        "providers": r.providers, "resource_types": r.resource_types,
        "applicability_criteria": r.applicability_criteria, "security_owner": r.security_owner,
        "engineering_owner": r.engineering_owner, "framework_refs": r.framework_refs,
        "exception_eligible": r.exception_eligible, "prevention_boundary": r.prevention_boundary,
        "limitations": r.limitations, "change_reason": r.change_reason, "content_digest": r.content_digest,
        "created_by": r.created_by, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at),
        "submitted_at": iso(r.submitted_at), "submitted_by": r.submitted_by, "approved_at": iso(r.approved_at),
        "superseded_at": iso(r.superseded_at), "retired_at": iso(r.retired_at), "lock_version": r.lock_version,
    }


def implementation_revision_view(r: m.ImplementationRevision) -> dict[str, Any]:
    return {
        "id": r.id, "implementation_id": r.implementation_id, "revision": r.revision,
        "control_revision_id": r.control_revision_id, "status": r.status, "policy_kind": r.policy_kind,
        "source_kind": r.source_kind, "source_ref": r.source_ref, "pinned_version": r.pinned_version,
        "content_digest": r.content_digest, "parameters": r.parameters,
        "assignment_settings": r.assignment_settings, "native_document": r.native_document,
        "capability_metadata": r.capability_metadata, "prerequisites": r.prerequisites,
        "limitations": r.limitations, "verification": r.verification, "change_reason": r.change_reason,
        "created_by": r.created_by, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at),
        "submitted_at": iso(r.submitted_at), "submitted_by": r.submitted_by, "approved_at": iso(r.approved_at),
        "superseded_at": iso(r.superseded_at), "lock_version": r.lock_version,
    }


def validation_view(v: m.ImplementationValidation) -> dict[str, Any]:
    return {
        "id": v.id, "implementation_revision_id": v.implementation_revision_id,
        "revision_digest": v.revision_digest, "outcome": v.outcome, "evaluator_id": v.evaluator_id,
        "evaluator_version": v.evaluator_version, "checks": v.checks, "test_results": v.test_results,
        "possible_duplicates": v.possible_duplicates, "integration_ready": v.integration_ready,
        "integration_blockers": v.integration_blockers, "validator_version": v.validator_version,
        "validated_at": iso(v.validated_at), "validated_by": v.validated_by,
    }


def binding_view(b: m.PolicyBinding) -> dict[str, Any]:
    return {
        "id": b.id, "control_id": b.control_id, "implementation_revision_id": b.implementation_revision_id,
        "provider": b.provider, "binding_kind": b.binding_kind, "native_id": b.native_id,
        "definition_ref": b.definition_ref, "definition_digest": b.definition_digest,
        "target_scope_id": b.target_scope_id, "settings": b.settings, "exclusions": b.exclusions,
        "source_repository": b.source_repository, "management": b.management, "origin": b.origin,
        "desired_state": b.desired_state, "observed_state": b.observed_state, "observed_at": iso(b.observed_at),
        "evidence_provenance": b.evidence_provenance, "created_at": iso(b.created_at),
    }


def snapshot_view(s: m.InventorySnapshot, resource_count: int | None = None) -> dict[str, Any]:
    return {
        "id": s.id, "provider": s.provider, "label": s.label, "source": s.source, "provenance": s.provenance,
        "collected_at": iso(s.collected_at), "content_digest": s.content_digest, "created_at": iso(s.created_at),
        "resource_count": resource_count,
    }


def resource_view(r: m.ResourceSnapshot) -> dict[str, Any]:
    return {
        "id": r.id, "snapshot_id": r.snapshot_id, "resource_id": r.resource_id, "name": r.name,
        "provider": r.provider, "resource_type": r.resource_type, "scope_id": r.scope_id,
        "configuration": r.configuration, "application": r.application, "owner": r.owner,
        "criticality": r.criticality, "collected_at": iso(r.collected_at), "source": r.source,
        "missing_fields": r.missing_fields,
    }


def evidence_view(e: m.ReadinessEvidence) -> dict[str, Any]:
    return {
        "id": e.id, "control_id": e.control_id, "application": e.application, "scope_id": e.scope_id,
        "prerequisite": e.prerequisite, "status": e.status, "summary": e.summary, "evidence_ref": e.evidence_ref,
        "provided_by": e.provided_by, "provenance": e.provenance, "collected_at": iso(e.collected_at),
        "created_at": iso(e.created_at), "created_by": e.created_by,
    }


def work_view(w: m.WorkReference) -> dict[str, Any]:
    return {
        "id": w.id, "kind": w.kind, "owner_team": w.owner_team, "title": w.title, "detail": w.detail,
        "external_system": w.external_system, "external_ref": w.external_ref, "status": w.status,
        "related_type": w.related_type, "related_id": w.related_id, "scope_id": w.scope_id,
        "created_at": iso(w.created_at), "created_by": w.created_by, "closed_at": iso(w.closed_at),
    }


def run_view(r: m.AssessmentRun) -> dict[str, Any]:
    return {
        "id": r.id, "control_id": r.control_id, "control_revision_id": r.control_revision_id,
        "implementation_revision_id": r.implementation_revision_id, "target_scope_id": r.target_scope_id,
        "inventory_snapshot_id": r.inventory_snapshot_id, "request_fixture_set_id": r.request_fixture_set_id,
        "status": r.status, "evaluator_id": r.evaluator_id, "evaluator_version": r.evaluator_version,
        "disclosure": r.disclosure, "inputs": r.inputs, "input_digest": r.input_digest, "rollup": r.rollup,
        "result_digest": r.result_digest, "blockers": r.blockers, "limitations": r.limitations,
        "assumptions": r.assumptions, "next_steps": r.next_steps, "confidence": r.confidence,
        "assessed_at": iso(r.assessed_at), "created_by": r.created_by,
    }


def result_view(r: m.AssessmentResult) -> dict[str, Any]:
    return {
        "id": r.id, "run_id": r.run_id, "subject_kind": r.subject_kind, "subject_id": r.subject_id,
        "subject_name": r.subject_name, "resource_type": r.resource_type, "scope_id": r.scope_id,
        "application": r.application, "owner": r.owner, "applicability": r.applicability,
        "configuration_result": r.configuration_result, "exception_disposition": r.exception_disposition,
        "exception_id": r.exception_id, "request_impact": r.request_impact, "readiness": r.readiness,
        "reasons": r.reasons, "evidence": r.evidence, "missing_fields": r.missing_fields, "baseline": r.baseline,
    }


def decision_view(d: m.ApprovalDecision) -> dict[str, Any]:
    return {
        "id": d.id, "subject_type": d.subject_type, "subject_id": d.subject_id, "subject_digest": d.subject_digest,
        "actor_id": d.actor_id, "role": d.role, "decision": d.decision, "rationale": d.rationale,
        "decided_at": iso(d.decided_at), "correlation_id": d.correlation_id,
    }


def plan_view(p: m.RolloutPlan) -> dict[str, Any]:
    return {
        "id": p.id, "control_id": p.control_id, "title": p.title, "provider": p.provider,
        "target_scope_id": p.target_scope_id, "implementation_revision_ids": p.implementation_revision_ids,
        "rings": p.rings, "pause_criteria": p.pause_criteria, "rollback_plan": p.rollback_plan,
        "prerequisite_changes": p.prerequisite_changes, "deployment_instructions": p.deployment_instructions,
        "stage": p.stage, "state": p.state, "state_reason": p.state_reason, "created_by": p.created_by,
        "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "lock_version": p.lock_version,
    }


def package_view(p: m.ChangePackage, include_manifest: bool = True) -> dict[str, Any]:
    out = {
        "id": p.id, "plan_id": p.plan_id, "control_id": p.control_id, "package_revision": p.package_revision,
        "through_stage": p.through_stage, "status": p.status, "manifest_digest": p.manifest_digest,
        "gate_results": p.gate_results, "status_reasons": p.status_reasons, "created_by": p.created_by,
        "created_at": iso(p.created_at), "status_changed_at": iso(p.status_changed_at),
        "lock_version": p.lock_version,
    }
    if include_manifest:
        out["manifest"] = p.manifest
    return out


def bundle_view(b: m.HandoffBundle, include_files: bool = False) -> dict[str, Any]:
    out = {
        "id": b.id, "package_id": b.package_id, "kind": b.kind, "bundle_digest": b.bundle_digest,
        "file_digests": b.file_digests, "adapter": b.adapter, "export_path": b.export_path,
        "export_checks": b.export_checks, "exported_at": iso(b.exported_at), "exported_by": b.exported_by,
    }
    if include_files:
        out["files"] = b.files
    return out


def target_view(t: m.DeliveryTarget) -> dict[str, Any]:
    return {
        "id": t.id, "bundle_id": t.bundle_id, "plan_id": t.plan_id, "control_id": t.control_id,
        "ring_stage": t.ring_stage, "scope_id": t.scope_id, "artifact_kind": t.artifact_kind,
        "native_identity": t.native_identity, "expected_digest": t.expected_digest, "enforcing": t.enforcing,
        "exception_id": t.exception_id, "binding_id": t.binding_id, "state": t.state,
        "state_changed_at": iso(t.state_changed_at), "state_basis_at": iso(t.state_basis_at),
        "last_receipt_id": t.last_receipt_id, "last_observation_id": t.last_observation_id,
        "verified_at": iso(t.verified_at), "provenance": t.provenance,
    }


def receipt_view(r: m.DeploymentReceipt) -> dict[str, Any]:
    return {
        "id": r.id, "receipt_key": r.receipt_key, "payload_digest": r.payload_digest, "bundle_id": r.bundle_id,
        "bundle_digest": r.bundle_digest, "ring_stage": r.ring_stage,
        "target_scope_native_id": r.target_scope_native_id, "pipeline_run_ref": r.pipeline_run_ref,
        "applied_native_identities": r.applied_native_identities, "result": r.result,
        "reported_completed_at": iso(r.reported_completed_at), "received_at": iso(r.received_at),
        "provenance": r.provenance, "validation_status": r.validation_status,
        "validation_notes": r.validation_notes, "delivery_target_ids": r.delivery_target_ids,
    }


def observation_view(o: m.Observation) -> dict[str, Any]:
    return {
        "id": o.id, "delivery_target_id": o.delivery_target_id, "binding_id": o.binding_id,
        "exception_id": o.exception_id, "native_identity": o.native_identity, "observed_state": o.observed_state,
        "observed_digest": o.observed_digest, "observed_at": iso(o.observed_at), "recorded_at": iso(o.recorded_at),
        "source": o.source, "provenance": o.provenance, "comparison": o.comparison, "differences": o.differences,
        "applied_to_state": o.applied_to_state, "notes": o.notes,
    }


def audit_view(a: m.AuditEvent) -> dict[str, Any]:
    return {
        "id": a.id, "occurred_at": iso(a.occurred_at), "actor_id": a.actor_id, "actor_roles": a.actor_roles,
        "action": a.action, "object_type": a.object_type, "object_id": a.object_id,
        "object_revision": a.object_revision, "reason": a.reason, "correlation_id": a.correlation_id,
        "before_digest": a.before_digest, "after_digest": a.after_digest, "scope_ids": a.scope_ids,
        "control_id": a.control_id, "details": a.details,
    }
