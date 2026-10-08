"""Guided drafts: unsaved proposals that become real only through the existing governed services.

A draft carries the *basis* it was prepared from (revision ids and content digests, the assessment
run it relied on, the duplicate check it passed). Submission recomputes that basis; any difference is
refused with ``DRAFT_STALE`` so a draft prepared against old revisions or old evidence can never be
submitted silently. After the basis check the payload is validated with the same strict request
models the Classic API uses and passed to the same service function, so authorization, validation,
optimistic locking and audit are exactly the Classic behaviour.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from pydantic import ValidationError
from sqlalchemy import select

from app.api.schemas import ControlCreate, ExceptionCreate, PlanCreate, Strict
from app.core.clock import iso
from app.core.digests import digest
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import entities as m
from app.models.enums import AssessmentStatus, PlanState, Role
from app.providers.registry import get_provider
from app.services import audit, controls, exceptions, rollouts, settings
from app.services.implementations import latest_validation, revision_digest
from app.workspace.tools import (
    ControlDraftInput,
    ExceptionDraftInput,
    Investigation,
    RolloutDraftInput,
    SearchControlsInput,
    _latest_impl_revision,
    search_controls,
)

SUBMIT_VIA = "POST /api/v1/workspace/drafts/submit (requires confirmed=true), which calls the existing {svc} service"


class DraftSubmit(Strict):
    kind: Literal["exception", "rollout_plan", "control"]
    basis: dict[str, Any]
    payload: dict[str, Any]
    confirmed: Literal[True]


def _control_revision_basis(control: m.Control) -> dict[str, Any]:
    crev = control.revisions[-1]
    return {"control_revision_id": crev.id, "control_revision_digest": digest(controls.revision_content(crev))}


def _resource(inv: Investigation, resource_id: str) -> m.ResourceSnapshot:
    row = inv.ctx.session.scalars(
        select(m.ResourceSnapshot).join(m.InventorySnapshot, m.InventorySnapshot.id == m.ResourceSnapshot.snapshot_id)
        .where(m.ResourceSnapshot.resource_id == resource_id)
        .order_by(m.InventorySnapshot.collected_at.desc()).limit(1)).first()
    if row is None or not inv.can_read(row.scope_id):
        raise NotFound("Resource not found")
    return row


# ------------------------------------------------------------------------------------ exception


def exception_basis(inv: Investigation, control: m.Control, resource: m.ResourceSnapshot | None) -> dict[str, Any]:
    provider = resource.provider if resource is not None else None
    irev = _latest_impl_revision(control, provider)
    return {**_control_revision_basis(control),
            "implementation_revision_id": irev.id if irev else None,
            "implementation_revision_digest": revision_digest(irev) if irev else None,
            "resource_snapshot_id": resource.snapshot_id if resource is not None else None}


def prepare_exception_draft(inv: Investigation, inp: ExceptionDraftInput) -> dict[str, Any]:
    control = inv.control(inp.control_id)
    crev = control.revisions[-1]
    resource = _resource(inv, inp.resource_id)
    irev = _latest_impl_revision(control, resource.provider)
    blocking = []
    if not crev.exception_eligible:
        blocking.append("This control is not exception-eligible.")
    if irev is None:
        blocking.append(f"No {resource.provider} implementation exists for this control.")
    if not inv.ctx.principal.has_role_at(Role.EXCEPTION_REQUESTER, resource.scope_id, inv.ctx.tree):
        blocking.append("Submitting requires the EXCEPTION_REQUESTER role at the resource's scope. You can still "
                        "review the draft and share it with the application team.")
    max_days = settings.get(inv.ctx.session, "risk_acceptance")["max_validity_days"].get(crev.severity, 90)
    expires = inv.ctx.now + timedelta(days=min(30, max_days))
    preview = None
    if irev is not None:
        transient = m.SecurityException(
            scope_id=resource.scope_id, granularity="RESOURCE", resource_ids=[resource.resource_id],
            principal_patterns=[], expires_at=expires, valid_from=inv.ctx.now, control_id=control.id)
        bindings = list(inv.ctx.session.scalars(select(m.PolicyBinding).where(
            m.PolicyBinding.provider == resource.provider)))
        rep = get_provider(resource.provider).validate_exception_representation(transient, irev, bindings,
                                                                                 inv.ctx.tree)
        preview = {"representability": rep.representability, "reason": rep.reason,
                   "native_expiry_supported": rep.native_expiry_supported}
    payload = {
        "control_id": control.id, "implementation_id": irev.implementation_id if irev else None,
        "application": resource.application or "", "team": inv.ctx.principal.team,
        "business_justification": "", "technical_justification": "", "scope_id": resource.scope_id,
        "granularity": "RESOURCE", "resource_ids": [resource.resource_id], "principal_patterns": [],
        "risk_owner": "", "compensating_controls": [], "valid_from": None, "expires_at": iso(expires),
        "work_ref": None,
    }
    return {
        "kind": "exception", "title": f"Exception request for {resource.name}",
        "basis": exception_basis(inv, control, resource), "payload": payload,
        "required_fields": ["business_justification", "technical_justification", "risk_owner",
                            "compensating_controls"],
        "can_submit": not blocking, "blocking_reasons": blocking, "preview": preview,
        "notes": [f"Maximum validity for {crev.severity} controls is {max_days} days; the suggested expiry is "
                  f"{min(30, max_days)} days.",
                  "Submitting creates a REQUESTED exception. A security approver who is not the requester decides it; "
                  "nothing is exempted until an approved exemption is delivered and observed.",
                  "Justifications, risk owner and compensating controls must be written by a person."],
        "submit_via": SUBMIT_VIA.format(svc="exception"),
    }


# ------------------------------------------------------------------------------------ rollout plan


def rollout_basis(inv: Investigation, control: m.Control, irev: m.ImplementationRevision | None) -> dict[str, Any]:
    run = None
    if irev is not None:
        run = inv.ctx.session.scalars(select(m.AssessmentRun).where(
            m.AssessmentRun.implementation_revision_id == irev.id,
            m.AssessmentRun.status == AssessmentStatus.COMPLETED).order_by(m.AssessmentRun.seq.desc()).limit(1)).first()
    active = None
    if irev is not None:
        active = inv.ctx.session.scalars(select(m.RolloutPlan.id).where(
            m.RolloutPlan.control_id == control.id, m.RolloutPlan.provider == irev.implementation.provider,
            m.RolloutPlan.state != PlanState.CANCELLED)).first()
    latest = irev.implementation.revisions[-1] if irev is not None else None
    return {**_control_revision_basis(control),
            "implementation_revision_id": irev.id if irev else None,
            "implementation_revision_digest": revision_digest(irev) if irev else None,
            "implementation_revision_is_latest": bool(latest is not None and latest.id == irev.id),
            "assessment_run_id": run.id if run else None,
            "assessment_result_digest": run.result_digest if run else None,
            "active_plan_id": active}


def _pick_implementation_revision(control: m.Control, irev_id: str | None) -> m.ImplementationRevision | None:
    for impl in control.implementations:
        for rev in impl.revisions:
            if irev_id is not None and rev.id == irev_id:
                return rev
    if irev_id is not None:
        raise NotFound("Implementation revision not found for this control")
    return _latest_impl_revision(control)


def prepare_rollout_draft(inv: Investigation, inp: RolloutDraftInput) -> dict[str, Any]:
    control = inv.control(inp.control_id)
    irev = _pick_implementation_revision(control, inp.implementation_revision_id)
    if irev is None:
        raise ValidationFailed("This control has no implementation to roll out.")
    basis = rollout_basis(inv, control, irev)
    blocking = []
    if not inv.ctx.principal.has_role(Role.CONTROL_ENGINEER):
        blocking.append("Creating a rollout plan requires the CONTROL_ENGINEER role.")
    if basis["active_plan_id"]:
        blocking.append(f"Plan {basis['active_plan_id']} is already active for this control; edit or cancel it in "
                        "the Classic Experience.")
    if basis["assessment_run_id"] is None:
        blocking.append("No completed assessment for this implementation revision; run one first so the pilot "
                        "suggestion and package gates have evidence.")
    val = latest_validation(inv.ctx, irev)
    if val is None or val.outcome != "PASS":
        blocking.append("The implementation revision has no passing validation.")
    template = rollouts.plan_template(inv.ctx, control.id, irev.id)
    payload = {k: template[k] for k in ("control_id", "implementation_revision_id", "target_scope_id", "title",
                                       "rings", "pause_criteria", "rollback_plan", "prerequisite_changes",
                                       "deployment_instructions")}
    return {
        "kind": "rollout_plan", "title": f"Rollout plan for {control.id}", "basis": basis, "payload": payload,
        "required_fields": [], "can_submit": not blocking, "blocking_reasons": blocking,
        "preview": {"capability_notes": template.get("capability_notes", {})},
        "notes": ["Suggested from provider capabilities, existing bindings and the latest assessment. Review every "
                  "ring before submitting.",
                  "A plan is not a package: the change package, its gates and two independent approvals follow in "
                  "the Classic Experience.",
                  "If the control, implementation or assessment changes before submission, this draft is refused as "
                  "stale and must be prepared again."],
        "submit_via": SUBMIT_VIA.format(svc="rollout plan"),
    }


# ------------------------------------------------------------------------------------ control


def control_basis(inv: Investigation, search: dict[str, Any]) -> dict[str, Any]:
    found = search_controls(inv, SearchControlsInput.model_validate(search))
    return {"search": search, "matching_control_ids": sorted(i["control_id"] for i in found["items"]
                                                             if i["score"] >= 10)}


def prepare_control_draft(inv: Investigation, inp: ControlDraftInput) -> dict[str, Any]:
    search = {"query": inp.problem_statement[:300], "provider": inp.provider, "resource_type": inp.resource_type}
    basis = control_basis(inv, search)
    blocking = []
    if not inv.ctx.principal.has_role(Role.CONTROL_ENGINEER):
        blocking.append("Proposing a control requires the CONTROL_ENGINEER role.")
    if basis["matching_control_ids"]:
        blocking.append("Existing control(s) already target this resource type: "
                        + ", ".join(basis["matching_control_ids"])
                        + ". Revise the existing control instead of creating a duplicate.")
    payload = {
        "id": "", "origin": "PROPOSED", "operational_owner": "Cloud Engineering",
        "change_reason": "Proposed from the AI Control Workspace", "name": "", "description": inp.problem_statement,
        "security_objective": "", "rationale": "", "source_evidence": [], "severity": "HIGH",
        "providers": [inp.provider] if inp.provider else [], "resource_types": [inp.resource_type]
        if inp.resource_type else [], "applicability_criteria": "", "security_owner": "", "engineering_owner": "",
        "framework_refs": [], "exception_eligible": True, "prevention_boundary": "", "limitations": [],
    }
    return {
        "kind": "control", "title": "New control proposal", "basis": basis, "payload": payload,
        "required_fields": ["id", "name", "security_objective", "rationale", "severity", "applicability_criteria",
                            "security_owner", "engineering_owner", "prevention_boundary"],
        "can_submit": not blocking, "blocking_reasons": blocking, "preview": None,
        "notes": ["Creates revision 1 as a DRAFT. Submission for review, implementation, validation and assessment "
                  "remain separate governed steps.",
                  "Severity, objective, rationale and prevention boundary are security decisions and must be written "
                  "by a person; nothing is inferred."],
        "submit_via": SUBMIT_VIA.format(svc="control"),
    }


# ------------------------------------------------------------------------------------ submit


def _validate(model, payload: dict[str, Any]):
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise ValidationFailed("Draft payload failed validation.",
                               details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]) from exc


def _stale(submitted: dict[str, Any], current: dict[str, Any]) -> None:
    changed = sorted(k for k in set(submitted) | set(current) if submitted.get(k) != current.get(k))
    if changed:
        raise Conflict("The draft is stale: the platform state it was prepared from has changed. Prepare it again.",
                       code="DRAFT_STALE", details={"changed": changed, "current_basis": current})


def submit_draft(inv: Investigation, body: DraftSubmit) -> dict[str, Any]:
    ctx = inv.ctx
    if body.kind == "exception":
        payload = _validate(ExceptionCreate, body.payload)
        control = inv.control(payload.control_id)
        resource = _resource(inv, payload.resource_ids[0]) if payload.resource_ids else None
        _stale(body.basis, exception_basis(inv, control, resource))
        exc = exceptions.request_exception(ctx, payload)
        created = {"type": "exception", "id": exc.id, "href": f"/exceptions/{exc.id}"}
        control_id, scope_ids = control.id, [exc.scope_id]
    elif body.kind == "rollout_plan":
        payload = _validate(PlanCreate, body.payload)
        control = inv.control(payload.control_id)
        irev = _pick_implementation_revision(control, payload.implementation_revision_id)
        _stale(body.basis, rollout_basis(inv, control, irev))
        plan = rollouts.create_plan(ctx, payload)
        created = {"type": "rollout_plan", "id": plan.id, "href": f"/controls/{control.id}"}
        control_id, scope_ids = control.id, [plan.target_scope_id]
    else:
        payload = _validate(ControlCreate, body.payload)
        search = body.basis.get("search")
        if not isinstance(search, dict):
            raise ValidationFailed("Draft basis is missing its duplicate check.")
        _stale(body.basis, control_basis(inv, search))
        control = controls.create_control(ctx, payload)
        created = {"type": "control", "id": control.id, "href": f"/controls/{control.id}"}
        control_id, scope_ids = control.id, []
    audit.record(ctx, action="workspace.draft_submitted", object_type=created["type"], object_id=created["id"],
                 control_id=control_id, scope_ids=scope_ids,
                 details={"source": "ai_control_workspace", "basis": body.basis, "confirmed": True})
    return {"created": created,
            "note": "Created through the existing governed service. Continue in the Classic Experience."}
