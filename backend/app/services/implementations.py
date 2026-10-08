"""Implementations: provider-specific mechanisms that implement a control revision."""

from __future__ import annotations

import copy

from sqlalchemy import select

from app.auth.principal import require_role
from app.core.digests import digest
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import RevisionStatus, Role, ValidationOutcome
from app.providers.registry import get_provider
from app.providers.verification import (
    AWS_VERIFICATION,
    AZURE_SEARCH_PNA,
    AZURE_SEARCH_PNA_DEFINITION,
    AZURE_VERIFICATION,
    UNVERIFIED_REFERENCE,
)
from app.services import audit
from app.services.context import RequestContext
from app.services.controls import get_control, latest_revision

VALIDATOR_VERSION = "1.0.0"
CONTENT_FIELDS = ["policy_kind", "source_kind", "source_ref", "pinned_version", "parameters", "assignment_settings",
                  "native_document", "prerequisites", "limitations"]


def verification_for(provider: str, policy_kind: str, source_ref: str, pinned_version: str | None) -> dict:
    """Verification status is decided by the server from recorded sources, never by the client."""
    if (provider == "azure" and policy_kind == "AZURE_POLICY_BUILTIN_ASSIGNMENT"
            and source_ref.lower() == AZURE_SEARCH_PNA["definition_id"].lower()
            and pinned_version == AZURE_SEARCH_PNA["version"]):
        return AZURE_VERIFICATION
    if provider == "aws" and policy_kind == "AWS_SCP":
        return AWS_VERIFICATION
    return UNVERIFIED_REFERENCE


def revision_digest(rev: m.ImplementationRevision) -> str:
    return digest({f: getattr(rev, f) for f in CONTENT_FIELDS} | {"control_revision_id": rev.control_revision_id})


def get_implementation(ctx: RequestContext, impl_id: str) -> m.Implementation:
    impl = ctx.session.get(m.Implementation, impl_id)
    if impl is None:
        raise NotFound(f"Implementation {impl_id} not found")
    return impl


def get_revision(ctx: RequestContext, rev_id: str, lock: bool = False) -> m.ImplementationRevision:
    rev = ctx.session.get(m.ImplementationRevision, rev_id, with_for_update=lock)
    if rev is None:
        raise NotFound(f"Implementation revision {rev_id} not found")
    return rev


def latest_validation(ctx: RequestContext, rev: m.ImplementationRevision) -> m.ImplementationValidation | None:
    return ctx.session.scalars(
        select(m.ImplementationValidation)
        .where(m.ImplementationValidation.implementation_revision_id == rev.id,
               m.ImplementationValidation.revision_digest == revision_digest(rev))
        .order_by(m.ImplementationValidation.seq.desc())
        .limit(1)
    ).first()


def _apply(rev: m.ImplementationRevision, data: dict, provider: str) -> None:
    data = dict(data)
    if (provider == "azure" and data.get("source_kind") == "BUILT_IN" and data.get("native_document") is None
            and data.get("source_ref", "").lower() == AZURE_SEARCH_PNA["definition_id"].lower()
            and data.get("pinned_version") == AZURE_SEARCH_PNA["version"]):
        # Reference to a verified built-in: pin the verified definition copy for evaluation and drift detection.
        data["native_document"] = copy.deepcopy(AZURE_SEARCH_PNA_DEFINITION)
    for f in CONTENT_FIELDS:
        setattr(rev, f, data[f])
    rev.content_digest = digest(rev.native_document) if rev.native_document is not None else None
    rev.verification = verification_for(provider, rev.policy_kind, rev.source_ref, rev.pinned_version)
    rev.capability_metadata = get_provider(provider).capabilities().to_dict()


def create_implementation(ctx: RequestContext, control_id: str, payload) -> m.Implementation:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Creating an implementation")
    control = get_control(ctx, control_id)
    crev = latest_revision(ctx, control.id)
    if crev is None or crev.status == RevisionStatus.RETIRED:
        raise ValidationFailed("Control has no active revision.")
    if payload.provider not in crev.providers:
        raise ValidationFailed(f"Control revision {crev.revision} does not list provider {payload.provider}.")
    get_provider(payload.provider)
    impl = m.Implementation(id=new_id("impl"), control_id=control.id, provider=payload.provider, name=payload.name,
                            mechanism_role=payload.mechanism_role, management=payload.management,
                            created_at=ctx.now, created_by=ctx.principal.user_id)
    ctx.session.add(impl)
    rev = m.ImplementationRevision(id=new_id("irev"), implementation_id=impl.id, revision=1,
                                   control_revision_id=crev.id, status=RevisionStatus.DRAFT,
                                   change_reason=payload.change_reason, created_by=ctx.principal.user_id,
                                   created_at=ctx.now, updated_at=ctx.now)
    _apply(rev, payload.model_dump(), payload.provider)
    ctx.session.add(rev)
    ctx.session.flush()
    audit.record(ctx, action="implementation.created", object_type="implementation", object_id=impl.id,
                 object_revision=1, reason=payload.change_reason, control_id=control.id,
                 after_digest=revision_digest(rev),
                 details={"provider": impl.provider, "policy_kind": rev.policy_kind,
                          "verification": rev.verification["status"]})
    return impl


def new_revision(ctx: RequestContext, impl_id: str, payload) -> m.ImplementationRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Authoring an implementation revision")
    impl = get_implementation(ctx, impl_id)
    latest = impl.revisions[-1] if impl.revisions else None
    if latest is not None and latest.status == RevisionStatus.DRAFT:
        raise Conflict("A draft revision already exists; edit it instead.")
    crev = latest_revision(ctx, impl.control_id)
    rev = m.ImplementationRevision(id=new_id("irev"), implementation_id=impl.id,
                                   revision=(latest.revision + 1) if latest else 1, control_revision_id=crev.id,
                                   status=RevisionStatus.DRAFT, change_reason=payload.change_reason,
                                   created_by=ctx.principal.user_id, created_at=ctx.now, updated_at=ctx.now)
    _apply(rev, payload.model_dump(), impl.provider)
    ctx.session.add(rev)
    ctx.session.flush()
    audit.record(ctx, action="implementation_revision.created", object_type="implementation_revision",
                 object_id=rev.id, object_revision=rev.revision, reason=payload.change_reason,
                 control_id=impl.control_id, after_digest=revision_digest(rev))
    return rev


def update_draft(ctx: RequestContext, rev_id: str, payload) -> m.ImplementationRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Editing an implementation revision")
    rev = get_revision(ctx, rev_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict("Submitted implementation revisions are immutable.", code="IMMUTABLE_REVISION")
    if rev.lock_version != payload.expected_lock_version:
        raise Conflict("Revision changed since you loaded it.", code="STALE_VERSION")
    impl = rev.implementation
    before = revision_digest(rev)
    _apply(rev, payload.model_dump(), impl.provider)
    rev.change_reason = payload.change_reason
    rev.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="implementation_revision.updated", object_type="implementation_revision",
                 object_id=rev.id, object_revision=rev.revision, reason=payload.change_reason,
                 before_digest=before, after_digest=revision_digest(rev), control_id=impl.control_id)
    return rev


def validate_revision(ctx: RequestContext, rev_id: str) -> m.ImplementationValidation:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Validating an implementation")
    rev = get_revision(ctx, rev_id)
    impl = rev.implementation
    provider = get_provider(impl.provider)
    bindings = list(ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider == impl.provider)))
    report = provider.validate_implementation(rev, ctx.tree, bindings)
    duplicates = provider.possible_duplicates(rev, [b for b in bindings if b.implementation_revision_id != rev.id],
                                              ctx.tree)
    v = m.ImplementationValidation(
        id=new_id("ival"), implementation_revision_id=rev.id, revision_digest=revision_digest(rev),
        outcome=report.outcome, evaluator_id=report.evaluator_id, evaluator_version=report.evaluator_version,
        checks=[c.to_dict() for c in report.checks], test_results=report.test_results,
        possible_duplicates=duplicates, integration_ready=report.integration_ready,
        integration_blockers=report.integration_blockers, validator_version=VALIDATOR_VERSION,
        validated_at=ctx.now, validated_by=ctx.principal.user_id)
    ctx.session.add(v)
    ctx.session.flush()
    audit.record(ctx, action="implementation_revision.validated", object_type="implementation_revision",
                 object_id=rev.id, object_revision=rev.revision, control_id=impl.control_id,
                 after_digest=v.revision_digest,
                 details={"outcome": v.outcome, "integration_ready": v.integration_ready,
                          "possible_duplicates": len(duplicates)})
    return v


def submit_revision(ctx: RequestContext, rev_id: str, expected_lock_version: int) -> m.ImplementationRevision:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Submitting an implementation revision")
    rev = get_revision(ctx, rev_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict(f"Revision is {rev.status}, not DRAFT.")
    if rev.lock_version != expected_lock_version:
        raise Conflict("Revision changed since you loaded it.", code="STALE_VERSION")
    v = latest_validation(ctx, rev)
    if v is None or v.outcome != ValidationOutcome.PASS:
        raise Conflict("Submit requires a passing validation of the current content.",
                       code="VALIDATION_REQUIRED",
                       details={"latest_outcome": v.outcome if v else None,
                                "checks": v.checks if v else []})
    rev.status = RevisionStatus.IN_REVIEW
    rev.submitted_at = ctx.now
    rev.submitted_by = ctx.principal.user_id
    rev.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="implementation_revision.submitted", object_type="implementation_revision",
                 object_id=rev.id, object_revision=rev.revision, control_id=rev.implementation.control_id,
                 after_digest=revision_digest(rev))
    return rev


def delete_draft(ctx: RequestContext, rev_id: str) -> dict:
    require_role(ctx.principal, Role.CONTROL_ENGINEER, action="Deleting a draft")
    rev = get_revision(ctx, rev_id, lock=True)
    if rev.status != RevisionStatus.DRAFT:
        raise Conflict("Only unreferenced drafts can be deleted.")
    for model, col in ((m.AssessmentRun, m.AssessmentRun.implementation_revision_id),
                       (m.ImplementationValidation, m.ImplementationValidation.implementation_revision_id),
                       (m.PolicyBinding, m.PolicyBinding.implementation_revision_id)):
        if ctx.session.scalars(select(model).where(col == rev.id).limit(1)).first():
            raise Conflict("Draft is referenced (validation, assessment or binding history) and is preserved.")
    control_id = rev.implementation.control_id
    ctx.session.delete(rev)
    ctx.session.flush()
    audit.record(ctx, action="implementation_revision.deleted", object_type="implementation_revision",
                 object_id=rev_id, object_revision=rev.revision, control_id=control_id)
    return {"deleted_revision_id": rev_id}
