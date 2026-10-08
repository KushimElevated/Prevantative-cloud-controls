"""Audit events are written in the same transaction as the domain change they describe."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.auth.principal import Principal
from app.core.context import current_correlation_id
from app.models.entities import AuditEvent
from app.services.context import RequestContext


def record(
    ctx: RequestContext,
    *,
    action: str,
    object_type: str,
    object_id: str,
    object_revision: Any = None,
    reason: str | None = None,
    before_digest: str | None = None,
    after_digest: str | None = None,
    scope_ids: list[str] | None = None,
    control_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    return record_raw(
        ctx.session,
        principal=ctx.principal,
        now=ctx.now,
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_revision=object_revision,
        reason=reason,
        before_digest=before_digest,
        after_digest=after_digest,
        scope_ids=scope_ids,
        control_id=control_id,
        details=details,
    )


def record_raw(
    session: Session,
    *,
    principal: Principal | None,
    now,
    action: str,
    object_type: str,
    object_id: str,
    object_revision: Any = None,
    reason: str | None = None,
    before_digest: str | None = None,
    after_digest: str | None = None,
    scope_ids: list[str] | None = None,
    control_id: str | None = None,
    details: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditEvent:
    event = AuditEvent(
        occurred_at=now,
        actor_id=principal.user_id if principal else None,
        actor_roles=principal.roles if principal else ["SYSTEM"],
        action=action,
        object_type=object_type,
        object_id=object_id,
        object_revision=None if object_revision is None else str(object_revision),
        reason=reason,
        correlation_id=correlation_id or current_correlation_id(),
        before_digest=before_digest,
        after_digest=after_digest,
        scope_ids=sorted(set(scope_ids or [])),
        control_id=control_id,
        details=details or {},
    )
    session.add(event)
    return event
