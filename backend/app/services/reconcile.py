"""Idempotent reconciliation / expiry command.

Scheduling seam: run `python -m app.cli reconcile` from any scheduler (cron, a CI schedule,
a container job). It is safe to run repeatedly. It records state transitions and creates
follow-up work references; it never rewrites policies or mutates cloud infrastructure.
Validity is also derived on every read, so a missed run cannot keep an expired approval active.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.core.clock import iso
from app.models import entities as m
from app.models.enums import DeliveryState, ExceptionStatus, NativeStatus
from app.services import audit, settings
from app.services.exceptions import OPEN_STATUSES, ensure_work, native_expiry_at


def reconcile(session: Session, now: datetime, actor: Principal | None) -> dict[str, Any]:
    summary: dict[str, list] = {"expired_exceptions": [], "removal_handoffs": [], "cleanup_items": [],
                                "renewal_reviews": [], "stale_verifications": [], "drift_items": [],
                                "work_references_created": []}
    fresh = settings.get(session, "evidence_freshness")
    warn_days = settings.get(session, "expiry")["warning_days"]
    obs_max = timedelta(hours=fresh["observation_max_age_hours"])

    def work(**kw):
        w, created = ensure_work(session, now, created_by=actor.user_id if actor else None, **kw)
        if created:
            summary["work_references_created"].append(w.id)
        return w, created

    for exc in session.scalars(select(m.SecurityException).order_by(m.SecurityException.id)):
        if exc.governance_status in OPEN_STATUSES and now >= exc.expires_at:
            before = exc.governance_status
            exc.governance_status = ExceptionStatus.EXPIRED
            exc.updated_at = now
            summary["expired_exceptions"].append(exc.id)
            audit.record_raw(session, principal=actor, now=now, action="exception.expired",
                             object_type="exception", object_id=exc.id, object_revision=exc.revision,
                             scope_ids=[exc.scope_id], control_id=exc.control_id,
                             reason="Validity window ended (reconciliation)", details={"before": before})
        if exc.governance_status in (ExceptionStatus.EXPIRED, ExceptionStatus.REVOKED) and \
                exc.native_status in (NativeStatus.APPLIED, NativeStatus.REMOVAL_PENDING):
            native_exp = native_expiry_at(exc)
            if exc.native_expiry_supported and native_exp is not None and native_exp <= max(exc.expires_at, now):
                _, created = work(kind="EXEMPTION_CLEANUP", owner_team="Cloud Engineering",
                                  title=f"Delete expired exemption object for {exc.id}",
                                  detail=f"Native expiresOn {iso(native_exp)} has passed, so the provider no longer "
                                         "honors it. The object remains for record-keeping; delete it through a "
                                         "reviewed change when convenient. The platform has not removed it.",
                                  related_type="exception", related_id=exc.id, scope_id=exc.scope_id,
                                  dedupe_key=f"cleanup:{exc.id}")
                if created:
                    summary["cleanup_items"].append(exc.id)
            else:
                if exc.native_status != NativeStatus.REMOVAL_PENDING:
                    exc.native_status = NativeStatus.REMOVAL_PENDING
                    exc.updated_at = now
                    audit.record_raw(session, principal=actor, now=now, action="exception.native_removal_pending",
                                     object_type="exception", object_id=exc.id, scope_ids=[exc.scope_id],
                                     control_id=exc.control_id,
                                     reason="No native expiry (or native expiry outlives approval)")
                _, created = work(kind="EXEMPTION_REMOVAL", owner_team="Cloud Engineering",
                                  title=f"Remove native exception representation for {exc.id}",
                                  detail="Governance validity ended but the native representation has no expiry "
                                         "(or outlives the approval) and is still in force. Remove it via a reviewed "
                                         "change and report a receipt. The platform has not removed anything.",
                                  related_type="exception", related_id=exc.id, scope_id=exc.scope_id,
                                  dedupe_key=f"removal:{exc.id}")
                if created:
                    summary["removal_handoffs"].append(exc.id)
        if exc.governance_status == ExceptionStatus.APPROVED and \
                timedelta(0) <= exc.expires_at - now <= timedelta(days=warn_days):
            _, created = work(kind="RENEWAL_REVIEW", owner_team=exc.team,
                              title=f"Exception {exc.id} expires {iso(exc.expires_at)}",
                              detail="Remediate before expiry or request a renewal (a new reviewed revision).",
                              related_type="exception", related_id=exc.id, scope_id=exc.scope_id,
                              dedupe_key=f"renewal:{exc.id}")
            if created:
                summary["renewal_reviews"].append(exc.id)

    for t in session.scalars(select(m.DeliveryTarget).order_by(m.DeliveryTarget.id)):
        if t.state == DeliveryState.VERIFIED and t.last_observation_id:
            obs = session.get(m.Observation, t.last_observation_id)
            if obs is not None and now - obs.observed_at > obs_max:
                _, created = work(kind="VERIFICATION_STALE", owner_team="Cloud Engineering",
                                  title=f"Verification of {t.native_identity} is stale",
                                  detail="Verified coverage requires a fresh matching observation. Re-observe.",
                                  related_type="delivery_target", related_id=t.id, scope_id=t.scope_id,
                                  dedupe_key=f"stale-verification:{t.id}:{obs.id}")
                if created:
                    summary["stale_verifications"].append(t.id)
        if t.state == DeliveryState.DRIFTED:
            _, created = work(kind="DRIFT", owner_team="Cloud Engineering",
                              title=f"Drift on {t.native_identity}",
                              detail="Observed state differs from the approved artifact. No automatic correction.",
                              related_type="delivery_target", related_id=t.id, scope_id=t.scope_id,
                              dedupe_key=f"drift:{t.id}:{t.last_observation_id}")
            if created:
                summary["drift_items"].append(t.id)

    for b in session.scalars(select(m.PolicyBinding).order_by(m.PolicyBinding.id)):
        if b.observed_state is not None and b.observed_state != b.desired_state:
            _, created = work(kind="DRIFT", owner_team="Cloud Engineering",
                              title=f"Binding {b.native_id} differs from its desired state",
                              detail="Observed native binding differs from the desired state in its source "
                                     "repository. Follow up with the owner; no automatic correction.",
                              related_type="policy_binding", related_id=b.id, scope_id=b.target_scope_id,
                              dedupe_key=f"binding-drift:{b.id}:{iso(b.observed_at)}")
            if created:
                summary["drift_items"].append(b.id)

    audit.record_raw(session, principal=actor, now=now, action="reconcile.run", object_type="system",
                     object_id="reconcile", details={k: len(v) for k, v in summary.items()})
    session.flush()
    return summary
