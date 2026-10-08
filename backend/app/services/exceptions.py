"""Exception governance.

Governance status and native implementation status are separate. Effective validity is always
derived from the clock on read, so a missed background task cannot keep an expired approval
active. Approvals never override inherited native denies.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.auth.principal import require_role
from app.core.clock import iso, parse_utc
from app.core.digests import digest
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import (
    DecisionValue,
    ExceptionDisposition,
    ExceptionStatus,
    NativeStatus,
    Representability,
    Role,
)
from app.providers.registry import get_provider
from app.services import audit, settings
from app.services.context import RequestContext
from app.services.controls import get_control, latest_revision

OPEN_STATUSES = {ExceptionStatus.REQUESTED, ExceptionStatus.SECURITY_REVIEW, ExceptionStatus.APPROVED}


def effective_status(exc: m.SecurityException, now: datetime) -> str:
    if exc.governance_status in OPEN_STATUSES and now >= exc.expires_at:
        return ExceptionStatus.EXPIRED
    return exc.governance_status


def native_expiry_at(exc: m.SecurityException) -> datetime | None:
    obs = exc.native_observed or {}
    raw = (obs.get("properties") or {}).get("expiresOn")
    if not raw:
        return None
    try:
        return parse_utc(raw)
    except ValueError:
        return None


def native_honored(exc: m.SecurityException, now: datetime) -> bool:
    """Whether the native representation is observed and (where supported) unexpired."""
    if exc.native_status not in (NativeStatus.APPLIED, NativeStatus.REMOVAL_PENDING) or exc.native_observed is None:
        return False
    if exc.native_expiry_supported:
        exp = native_expiry_at(exc)
        return exp is None or exp > now
    return True


def disposition(exc: m.SecurityException, now: datetime) -> str:
    if exc.representability != Representability.REPRESENTABLE:
        return ExceptionDisposition.UNSUPPORTED
    st = effective_status(exc, now)
    if st == ExceptionStatus.EXPIRED:
        return ExceptionDisposition.EXPIRED
    if st in (ExceptionStatus.REQUESTED, ExceptionStatus.SECURITY_REVIEW):
        return ExceptionDisposition.PENDING
    if st == ExceptionStatus.APPROVED:
        if now < exc.valid_from:
            return ExceptionDisposition.APPROVED_UNAPPLIED
        return ExceptionDisposition.EFFECTIVE if native_honored(exc, now) else ExceptionDisposition.APPROVED_UNAPPLIED
    return ExceptionDisposition.NONE


DISPOSITION_PRIORITY = [
    ExceptionDisposition.EFFECTIVE, ExceptionDisposition.APPROVED_UNAPPLIED, ExceptionDisposition.PENDING,
    ExceptionDisposition.EXPIRED, ExceptionDisposition.UNSUPPORTED, ExceptionDisposition.NONE,
]


def content_digest(exc: m.SecurityException) -> str:
    return digest({
        "control_id": exc.control_id, "implementation_id": exc.implementation_id, "scope_id": exc.scope_id,
        "granularity": exc.granularity, "resource_ids": sorted(exc.resource_ids or []),
        "principal_patterns": sorted(exc.principal_patterns or []), "application": exc.application,
        "business_justification": exc.business_justification,
        "technical_justification": exc.technical_justification, "risk_owner": exc.risk_owner,
        "compensating_controls": exc.compensating_controls, "valid_from": iso(exc.valid_from),
        "expires_at": iso(exc.expires_at), "renews_exception_id": exc.renews_exception_id,
    })


def exception_view(exc: m.SecurityException, now: datetime, warning_days: int = 14,
                   decisions: list[m.ApprovalDecision] | None = None) -> dict[str, Any]:
    eff = effective_status(exc, now)
    disp = disposition(exc, now)
    days_left = (exc.expires_at - now).total_seconds() / 86400
    cleanup_pending = exc.native_status == NativeStatus.REMOVAL_PENDING or (
        eff in (ExceptionStatus.EXPIRED, ExceptionStatus.REVOKED) and exc.native_status == NativeStatus.APPLIED)
    return {
        "id": exc.id, "lineage_id": exc.lineage_id, "revision": exc.revision,
        "renews_exception_id": exc.renews_exception_id, "control_id": exc.control_id,
        "implementation_id": exc.implementation_id, "binding_ids": exc.binding_ids,
        "requester_id": exc.requester_id, "application": exc.application, "team": exc.team,
        "business_justification": exc.business_justification,
        "technical_justification": exc.technical_justification, "scope_id": exc.scope_id,
        "granularity": exc.granularity, "resource_ids": exc.resource_ids,
        "principal_patterns": exc.principal_patterns, "risk_owner": exc.risk_owner,
        "compensating_controls": exc.compensating_controls, "valid_from": iso(exc.valid_from),
        "expires_at": iso(exc.expires_at), "governance_status": exc.governance_status,
        "effective_status": eff, "disposition": disp, "native_status": exc.native_status,
        "native_expiry_supported": exc.native_expiry_supported, "native_expires_at": iso(native_expiry_at(exc)),
        "native_observed_at": iso(exc.native_observed_at), "native_provenance": exc.native_provenance,
        "native_observed": exc.native_observed, "representability": exc.representability,
        "representability_reason": exc.representability_reason,
        "native_representation": exc.native_representation, "work_ref": exc.work_ref,
        "expiring_soon": eff == ExceptionStatus.APPROVED and 0 <= days_left <= warning_days,
        "days_until_expiry": round(days_left, 1),
        "native_cleanup_pending": cleanup_pending,
        "created_at": iso(exc.created_at), "updated_at": iso(exc.updated_at), "lock_version": exc.lock_version,
        "content_digest": content_digest(exc),
        "decisions": [
            {"id": d.id, "actor_id": d.actor_id, "role": d.role, "decision": d.decision, "rationale": d.rationale,
             "decided_at": iso(d.decided_at), "subject_digest": d.subject_digest}
            for d in (decisions or [])
        ],
    }


def get_exception(ctx: RequestContext, exc_id: str, lock: bool = False) -> m.SecurityException:
    exc = ctx.session.get(m.SecurityException, exc_id, with_for_update=lock)
    if exc is None or not ctx.principal.can_read_scope(exc.scope_id, ctx.tree):
        raise NotFound(f"Exception {exc_id} not found")
    return exc


def decisions_for(ctx: RequestContext, exc_id: str) -> list[m.ApprovalDecision]:
    return list(ctx.session.scalars(select(m.ApprovalDecision).where(
        m.ApprovalDecision.subject_type == "EXCEPTION", m.ApprovalDecision.subject_id == exc_id)
        .order_by(m.ApprovalDecision.decided_at)))


def _implementation_for(ctx: RequestContext, control_id: str, implementation_id: str | None, scope_provider: str):
    impls = [i for i in get_control(ctx, control_id).implementations if i.provider == scope_provider]
    if implementation_id:
        impls = [i for i in impls if i.id == implementation_id]
    if not impls:
        raise ValidationFailed("No implementation of this control exists for the exception's provider.")
    impl = impls[0]
    rev = impl.revisions[-1]
    return impl, rev


def _represent(ctx: RequestContext, exc: m.SecurityException, impl, rev) -> None:
    provider = get_provider(impl.provider)
    bindings = list(ctx.session.scalars(select(m.PolicyBinding).where(m.PolicyBinding.provider == impl.provider)))
    rep = provider.validate_exception_representation(exc, rev, bindings, ctx.tree)
    exc.representability = rep.representability
    exc.representability_reason = rep.reason
    exc.native_representation = rep.native_representation
    exc.native_expiry_supported = rep.native_expiry_supported


def _validate_window(ctx: RequestContext, control_id: str, valid_from: datetime, expires_at: datetime) -> None:
    crev = latest_revision(ctx, control_id)
    max_days = settings.get(ctx.session, "risk_acceptance")["max_validity_days"].get(crev.severity, 90)
    if expires_at <= ctx.now:
        raise ValidationFailed("Expiration must be in the future.")
    if expires_at <= valid_from:
        raise ValidationFailed("Expiration must be after the start of validity.")
    if expires_at - valid_from > timedelta(days=max_days):
        raise ValidationFailed(f"Validity exceeds the configured maximum of {max_days} days for "
                               f"{crev.severity} controls.")


def request_exception(ctx: RequestContext, payload) -> m.SecurityException:
    scope = ctx.tree.get(payload.scope_id)
    require_role(ctx.principal, Role.EXCEPTION_REQUESTER, scope_id=scope.id, tree=ctx.tree,
                 action="Requesting an exception")
    control = get_control(ctx, payload.control_id)
    crev = latest_revision(ctx, control.id)
    if not crev.exception_eligible:
        raise ValidationFailed("This control is not exception-eligible.")
    impl, rev = _implementation_for(ctx, control.id, payload.implementation_id, scope.provider)
    valid_from = payload.valid_from or ctx.now
    _validate_window(ctx, control.id, valid_from, payload.expires_at)
    for rid in payload.resource_ids:
        res = ctx.session.scalars(select(m.ResourceSnapshot).where(m.ResourceSnapshot.resource_id == rid).limit(1)
                                  ).first()
        if res is None or not ctx.tree.is_within(res.scope_id, scope.id):
            raise ValidationFailed(f"Resource {rid} is not a known resource within scope {scope.id}.")
        if res.application and res.application != payload.application:
            raise Forbidden(f"Resource {rid} belongs to application {res.application}, not {payload.application}.")
    exc_id = new_id("exc")
    exc = m.SecurityException(
        id=exc_id, lineage_id=exc_id, revision=1, renews_exception_id=None, control_id=control.id,
        implementation_id=impl.id, binding_ids=[], requester_id=ctx.principal.user_id,
        application=payload.application, team=payload.team, business_justification=payload.business_justification,
        technical_justification=payload.technical_justification, scope_id=scope.id,
        granularity=payload.granularity, resource_ids=payload.resource_ids,
        principal_patterns=payload.principal_patterns, risk_owner=payload.risk_owner,
        compensating_controls=payload.compensating_controls, valid_from=valid_from, expires_at=payload.expires_at,
        governance_status=ExceptionStatus.REQUESTED, native_status=NativeStatus.NOT_REQUESTED,
        representability="", representability_reason="", native_representation=None, native_expiry_supported=False,
        work_ref=payload.work_ref, created_at=ctx.now, updated_at=ctx.now)
    _represent(ctx, exc, impl, rev)
    ctx.session.add(exc)
    ctx.session.flush()
    audit.record(ctx, action="exception.requested", object_type="exception", object_id=exc.id, object_revision=1,
                 scope_ids=[scope.id], control_id=control.id, after_digest=content_digest(exc),
                 details={"representability": exc.representability})
    return exc


def start_review(ctx: RequestContext, exc_id: str, expected_lock_version: int) -> m.SecurityException:
    exc = get_exception(ctx, exc_id, lock=True)
    require_role(ctx.principal, Role.SECURITY_APPROVER, scope_id=exc.scope_id, tree=ctx.tree,
                 action="Starting exception review")
    if exc.lock_version != expected_lock_version:
        raise Conflict("Exception changed since you loaded it.", code="STALE_VERSION")
    if effective_status(exc, ctx.now) != ExceptionStatus.REQUESTED:
        raise Conflict(f"Exception is {effective_status(exc, ctx.now)}, not REQUESTED.")
    exc.governance_status = ExceptionStatus.SECURITY_REVIEW
    exc.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="exception.review_started", object_type="exception", object_id=exc.id,
                 object_revision=exc.revision, scope_ids=[exc.scope_id], control_id=exc.control_id)
    return exc


def decide(ctx: RequestContext, exc_id: str, decision: str, rationale: str, expected_lock_version: int
           ) -> m.SecurityException:
    exc = get_exception(ctx, exc_id, lock=True)
    authority = settings.get(ctx.session, "risk_acceptance")["authority_role"]
    require_role(ctx.principal, Role(authority), scope_id=exc.scope_id, tree=ctx.tree,
                 action="Deciding an exception (risk acceptance authority)")
    if exc.requester_id == ctx.principal.user_id:
        raise Forbidden("Requesters cannot decide their own exceptions.", code="SELF_APPROVAL")
    if exc.lock_version != expected_lock_version:
        raise Conflict("Exception changed since you loaded it.", code="STALE_VERSION")
    eff = effective_status(exc, ctx.now)
    if eff not in (ExceptionStatus.REQUESTED, ExceptionStatus.SECURITY_REVIEW):
        raise Conflict(f"Exception is {eff}; only pending requests can be decided.")
    if decision == DecisionValue.APPROVE and exc.representability != Representability.REPRESENTABLE:
        raise Conflict("This exception cannot be implemented natively and cannot be approved as an exception: "
                       f"{exc.representability_reason}", code=exc.representability)
    if decision == DecisionValue.APPROVE:
        _validate_window(ctx, exc.control_id, exc.valid_from, exc.expires_at)
    d = m.ApprovalDecision(id=new_id("dec"), subject_type="EXCEPTION", subject_id=exc.id,
                           subject_digest=content_digest(exc), actor_id=ctx.principal.user_id, role=authority,
                           decision=decision, rationale=rationale, decided_at=ctx.now,
                           correlation_id=audit.current_correlation_id())
    ctx.session.add(d)
    before = exc.governance_status
    if decision == DecisionValue.APPROVE:
        exc.governance_status = ExceptionStatus.APPROVED
        exc.native_status = NativeStatus.PENDING
    else:
        exc.governance_status = ExceptionStatus.REJECTED
    exc.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="exception.approved" if decision == DecisionValue.APPROVE else "exception.rejected",
                 object_type="exception", object_id=exc.id, object_revision=exc.revision, reason=rationale,
                 scope_ids=[exc.scope_id], control_id=exc.control_id, after_digest=d.subject_digest,
                 details={"before": before, "after": exc.governance_status, "decision_id": d.id})
    return exc


def revoke(ctx: RequestContext, exc_id: str, rationale: str, expected_lock_version: int) -> m.SecurityException:
    exc = get_exception(ctx, exc_id, lock=True)
    require_role(ctx.principal, Role.SECURITY_APPROVER, scope_id=exc.scope_id, tree=ctx.tree,
                 action="Revoking an exception")
    if exc.lock_version != expected_lock_version:
        raise Conflict("Exception changed since you loaded it.", code="STALE_VERSION")
    if effective_status(exc, ctx.now) != ExceptionStatus.APPROVED:
        raise Conflict("Only currently approved exceptions can be revoked.")
    d = m.ApprovalDecision(id=new_id("dec"), subject_type="EXCEPTION", subject_id=exc.id,
                           subject_digest=content_digest(exc), actor_id=ctx.principal.user_id,
                           role=Role.SECURITY_APPROVER, decision=DecisionValue.REVOKE, rationale=rationale,
                           decided_at=ctx.now, correlation_id=audit.current_correlation_id())
    ctx.session.add(d)
    exc.governance_status = ExceptionStatus.REVOKED
    if exc.native_status == NativeStatus.APPLIED:
        exc.native_status = NativeStatus.REMOVAL_PENDING
        ensure_work(ctx.session, ctx.now, kind="EXEMPTION_REMOVAL", owner_team="Cloud Engineering",
                    title=f"Remove native exemption for revoked exception {exc.id}",
                    detail="Governance approval was revoked. The native exemption still exists until Cloud "
                           "Engineering removes it through a reviewed change; this platform does not remove it.",
                    related_type="exception", related_id=exc.id, scope_id=exc.scope_id,
                    dedupe_key=f"removal:{exc.id}")
    elif exc.native_status == NativeStatus.PENDING:
        exc.native_status = NativeStatus.NOT_REQUESTED
    exc.updated_at = ctx.now
    ctx.session.flush()
    audit.record(ctx, action="exception.revoked", object_type="exception", object_id=exc.id,
                 object_revision=exc.revision, reason=rationale, scope_ids=[exc.scope_id],
                 control_id=exc.control_id, details={"native_status": exc.native_status})
    return exc


def renew(ctx: RequestContext, exc_id: str, payload) -> m.SecurityException:
    old = get_exception(ctx, exc_id)
    require_role(ctx.principal, Role.EXCEPTION_REQUESTER, scope_id=old.scope_id, tree=ctx.tree,
                 action="Requesting an exception renewal")
    eff = effective_status(old, ctx.now)
    if eff not in (ExceptionStatus.APPROVED, ExceptionStatus.EXPIRED):
        raise Conflict("Only approved or expired exceptions can be renewed.")
    pending = ctx.session.scalars(select(m.SecurityException).where(
        m.SecurityException.renews_exception_id == old.id,
        m.SecurityException.governance_status.in_([ExceptionStatus.REQUESTED, ExceptionStatus.SECURITY_REVIEW]))
    ).first()
    if pending:
        raise Conflict(f"Renewal {pending.id} is already pending.")
    valid_from = max(ctx.now, old.expires_at) if eff == ExceptionStatus.APPROVED else ctx.now
    _validate_window(ctx, old.control_id, valid_from, payload.expires_at)
    latest_rev = max(e.revision for e in ctx.session.scalars(select(m.SecurityException).where(
        m.SecurityException.lineage_id == old.lineage_id)))
    new = m.SecurityException(
        id=new_id("exc"), lineage_id=old.lineage_id, revision=latest_rev + 1, renews_exception_id=old.id,
        control_id=old.control_id, implementation_id=old.implementation_id, binding_ids=old.binding_ids,
        requester_id=ctx.principal.user_id, application=old.application, team=old.team,
        business_justification=payload.business_justification,
        technical_justification=payload.technical_justification, scope_id=old.scope_id,
        granularity=old.granularity, resource_ids=old.resource_ids, principal_patterns=old.principal_patterns,
        risk_owner=old.risk_owner, compensating_controls=payload.compensating_controls, valid_from=valid_from,
        expires_at=payload.expires_at, governance_status=ExceptionStatus.REQUESTED,
        native_status=NativeStatus.NOT_REQUESTED, representability="", representability_reason="",
        native_representation=None, native_expiry_supported=False, work_ref=old.work_ref,
        created_at=ctx.now, updated_at=ctx.now)
    impl = ctx.session.get(m.Implementation, old.implementation_id)
    _represent(ctx, new, impl, impl.revisions[-1])
    ctx.session.add(new)
    ctx.session.flush()
    audit.record(ctx, action="exception.renewal_requested", object_type="exception", object_id=new.id,
                 object_revision=new.revision, scope_ids=[new.scope_id], control_id=new.control_id,
                 after_digest=content_digest(new),
                 details={"renews": old.id, "previous_expires_at": iso(old.expires_at)})
    return new


def ensure_work(session, now, *, kind: str, owner_team: str, title: str, detail: str, related_type: str,
                related_id: str, scope_id: str | None, dedupe_key: str, created_by: str | None = None
                ) -> tuple[m.WorkReference, bool]:
    existing = session.scalars(select(m.WorkReference).where(m.WorkReference.dedupe_key == dedupe_key)).first()
    if existing:
        return existing, False
    w = m.WorkReference(id=new_id("work"), kind=kind, owner_team=owner_team, title=title, detail=detail,
                        external_system=None, external_ref=None, status="OPEN", related_type=related_type,
                        related_id=related_id, scope_id=scope_id, dedupe_key=dedupe_key, created_at=now,
                        created_by=created_by)
    session.add(w)
    session.flush()
    return w, True
