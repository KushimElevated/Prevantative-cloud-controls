"""Control catalog: security intent independent of provider syntax."""

from __future__ import annotations

from sqlalchemy import func, select

from app.api.schemas import ControlCreate, ControlRevisionUpdate
from app.auth.principal import require_role
from app.core.digests import digest
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import ControlOrigin, RevisionStatus, Role
from app.services import audit
from app.services.context import RequestContext

CONTENT_FIELDS = [
    "name", "description", "security_objective", "rationale", "source_evidence", "severity", "providers",
    "resource_types", "applicability_criteria", "security_owner", "engineering_owner", "framework_refs",
    "exception_eligible", "prevention_boundary", "limitations",
]


def revision_content(rev: m.ControlRevision) -> dict:
    return {f: getattr(rev, f) for f in CONTENT_FIELDS}


def get_control(ctx: RequestContext, control_id: str) -> m.Control:
    control = ctx.session.get(m.Control, control_id)
    if control is None:
        raise NotFound(f"Control {control_id} not found")
    return control


def get_revision(ctx: RequestContext, revision_id: str, lock: bool = False) -> m.ControlRevision:
    rev = ctx.session.get(m.ControlRevision, revision_id, with_for_update=lock)
    if rev is None:
        raise NotFound(f"Control revision {revision_id} not found")
    return rev


def latest_revision(ctx: RequestContext, control_id: str, *, non_draft: bool = False) -> m.ControlRevision | None:
    q = select(m.ControlRevision).where(m.ControlRevision.control_id == control_id)
    if non_draft:
        q = q.where(m.ControlRevision.status != RevisionStatus.DRAFT)
    return ctx.session.scalars(q.order_by(m.ControlRevision.revision.desc()).limit(1)).first()


def _apply_content(rev: m.ControlRevision, payload) -> None:
    data = payload.model_dump()
    for f in CONTENT_FIELDS:
        setattr(rev, f, data[f])


def create_control(ctx: RequestContext, payload: ControlCreate) -> m.Control:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Proposing or registering a control")
    if ctx.session.get(m.Control, payload.id):
        raise Conflict(f"Control {payload.id} already exists")
    control = m.Control(id=payload.id, origin=payload.origin, operational_owner=payload.operational_owner,
                        created_at=ctx.now, created_by=ctx.principal.user_id)
    ctx.session.add(control)
    rev = m.ControlRevision(id=new_id("crev"), control_id=control.id, revision=1, status=RevisionStatus.DRAFT,
                            change_reason=payload.change_reason, created_by=ctx.principal.user_id,
                            created_at=ctx.now, updated_at=ctx.now)
    _apply_content(rev, payload)
    ctx.session.add(rev)
    ctx.session.flush()
    note = None
    if payload.origin != ControlOrigin.PROPOSED:
        note = (f"Registered as {payload.origin}; operational ownership remains with {payload.operational_owner}. "
                "Cataloguing does not transfer ownership.")
    audit.record(ctx, action="control.created", object_type="control", object_id=control.id, object_revision=1,
                 reason=payload.change_reason, after_digest=digest(revision_content(rev)), control_id=control.id,
                 details={"origin": payload.origin, "note": note})
    return control


def new_revision(ctx: RequestContext, control_id: str, change_reason: str) -> m.ControlRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Authoring a control revision")
    control = get_control(ctx, control_id)
    latest = latest_revision(ctx, control.id)
    if latest is None:
        raise ValidationFailed("Control has no revisions")
    if latest.status == RevisionStatus.DRAFT:
        raise Conflict(f"Draft revision {latest.revision} already exists; edit it instead.")
    if latest.status == RevisionStatus.RETIRED:
        raise Conflict("Control is retired.")
    rev = m.ControlRevision(id=new_id("crev"), control_id=control.id, revision=latest.revision + 1,
                            status=RevisionStatus.DRAFT, change_reason=change_reason,
                            created_by=ctx.principal.user_id, created_at=ctx.now, updated_at=ctx.now,
                            **revision_content(latest))
    ctx.session.add(rev)
    ctx.session.flush()
    audit.record(ctx, action="control_revision.created", object_type="control_revision", object_id=rev.id,
                 object_revision=rev.revision, reason=change_reason, control_id=control.id,
                 after_digest=digest(revision_content(rev)), details={"based_on": latest.id})
    return rev


def update_draft(ctx: RequestContext, revision_id: str, payload: ControlRevisionUpdate) -> m.ControlRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Editing a control revision")
    rev = get_revision(ctx, revision_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict(f"Revision {rev.revision} is {rev.status}; submitted revisions are immutable. "
                       "Create a new revision.", code="IMMUTABLE_REVISION")
    if rev.lock_version != payload.expected_lock_version:
        raise Conflict("Revision changed since you loaded it.", code="STALE_VERSION",
                       details={"current_lock_version": rev.lock_version})
    before = digest(revision_content(rev))
    _apply_content(rev, payload)
    rev.change_reason = payload.change_reason
    rev.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="control_revision.updated", object_type="control_revision", object_id=rev.id,
                 object_revision=rev.revision, reason=payload.change_reason, before_digest=before,
                 after_digest=digest(revision_content(rev)), control_id=rev.control_id)
    return rev


def submit_revision(ctx: RequestContext, revision_id: str, expected_lock_version: int) -> m.ControlRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Submitting a control revision")
    rev = get_revision(ctx, revision_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict(f"Revision is {rev.status}, not DRAFT.")
    if rev.lock_version != expected_lock_version:
        raise Conflict("Revision changed since you loaded it.", code="STALE_VERSION")
    missing = [f for f in ("security_objective", "prevention_boundary", "security_owner", "engineering_owner")
               if not getattr(rev, f)]
    if not rev.source_evidence:
        missing.append("source_evidence")
    if not rev.limitations:
        missing.append("limitations")
    if missing:
        raise ValidationFailed("Revision is incomplete; every prevention claim needs scope, evidence and limits.",
                               details={"missing": missing})
    rev.content_digest = digest(revision_content(rev))
    rev.status = RevisionStatus.IN_REVIEW
    rev.submitted_at = ctx.now
    rev.submitted_by = ctx.principal.user_id
    rev.updated_at = ctx.now
    for older in ctx.session.scalars(select(m.ControlRevision).where(
            m.ControlRevision.control_id == rev.control_id, m.ControlRevision.id != rev.id,
            m.ControlRevision.status == RevisionStatus.IN_REVIEW)):
        older.status = RevisionStatus.SUPERSEDED
        older.superseded_at = ctx.now
        audit.record(ctx, action="control_revision.superseded", object_type="control_revision", object_id=older.id,
                     object_revision=older.revision, control_id=rev.control_id,
                     reason=f"Superseded by revision {rev.revision} before approval")
    ctx.session.flush()
    audit.record(ctx, action="control_revision.submitted", object_type="control_revision", object_id=rev.id,
                 object_revision=rev.revision, after_digest=rev.content_digest, control_id=rev.control_id)
    return rev


def delete_draft(ctx: RequestContext, revision_id: str) -> dict:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Deleting a draft")
    rev = get_revision(ctx, revision_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict("Only unreferenced drafts can be deleted; submitted revisions are preserved.")
    refs = ctx.session.scalar(select(func.count()).select_from(m.ImplementationRevision).where(
        m.ImplementationRevision.control_revision_id == rev.id))
    refs += ctx.session.scalar(select(func.count()).select_from(m.AssessmentRun).where(
        m.AssessmentRun.control_revision_id == rev.id))
    if refs:
        raise Conflict("Draft is referenced by implementations or assessments; it cannot be deleted.")
    control_id, revision = rev.control_id, rev.revision
    ctx.session.delete(rev)
    ctx.session.flush()
    deleted_control = False
    control = ctx.session.get(m.Control, control_id)
    remaining = ctx.session.scalar(select(func.count()).select_from(m.ControlRevision).where(
        m.ControlRevision.control_id == control_id))
    dependents = sum(ctx.session.scalar(select(func.count()).select_from(t).where(t.control_id == control_id))
                     for t in (m.Implementation, m.SecurityException, m.RolloutPlan, m.ReadinessEvidence,
                               m.PolicyBinding))
    if remaining == 0 and dependents == 0 and control is not None:
        ctx.session.delete(control)
        deleted_control = True
    audit.record(ctx, action="control_revision.deleted", object_type="control_revision", object_id=revision_id,
                 object_revision=revision, control_id=control_id,
                 details={"control_deleted": deleted_control})
    return {"deleted_revision_id": revision_id, "control_deleted": deleted_control}


def retire_control(ctx: RequestContext, control_id: str, reason: str) -> m.Control:
    require_role(ctx.principal, Role.SECURITY_APPROVER, action="Retiring a control")
    control = get_control(ctx, control_id)
    active_plans = ctx.session.scalar(select(func.count()).select_from(m.RolloutPlan).where(
        m.RolloutPlan.control_id == control_id, m.RolloutPlan.state != "CANCELLED"))
    if active_plans:
        raise Conflict("Cancel active rollout plans first; retirement does not remove deployed policies.")
    for rev in control.revisions:
        if rev.status in (RevisionStatus.APPROVED, RevisionStatus.IN_REVIEW):
            rev.status = RevisionStatus.RETIRED
            rev.retired_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="control.retired", object_type="control", object_id=control_id, reason=reason,
                 control_id=control_id)
    return control
