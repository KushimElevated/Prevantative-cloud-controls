from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select

from app.api.deps import Page, get_ctx, paged
from app.api.schemas import RoleGrant, SettingUpdate
from app.auth.principal import require_any_role, require_global_role
from app.core.clock import iso
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.ids import new_id
from app.models import entities as m
from app.models.enums import Role
from app.services import audit, dashboard, reconcile, settings
from app.services.context import RequestContext
from app.services.views import audit_view

router = APIRouter(tags=["admin", "audit", "dashboard"])


@router.get("/dashboard/metrics")
def metrics(ctx: RequestContext = Depends(get_ctx)):
    return dashboard.metrics(ctx)


@router.get("/audit")
def list_audit(page: Page = Depends(), object_type: str | None = None, object_id: str | None = None,
               control_id: str | None = None, correlation_id: str | None = None, action: str | None = None,
               ctx: RequestContext = Depends(get_ctx)):
    rows, total = audit.query_events(ctx, {"object_type": object_type, "object_id": object_id,
                                           "control_id": control_id, "correlation_id": correlation_id,
                                           "action": action}, limit=page.limit, offset=page.offset)
    return paged([audit_view(a) for a in rows], total, page)


@router.post("/admin/reconcile")
def run_reconcile(ctx: RequestContext = Depends(get_ctx)):
    require_any_role(ctx.principal, [Role.ADMIN, Role.CLOUD_ENGINEER], action="Running reconciliation")
    summary = reconcile.reconcile(ctx.session, ctx.now, ctx.principal)
    ctx.commit()
    return {"summary": summary, "ran_at": iso(ctx.now),
            "note": "Records transitions and follow-up work only. No cloud mutations or policy rewrites."}


@router.get("/admin/settings")
def get_settings(ctx: RequestContext = Depends(get_ctx)):
    rows = ctx.session.scalars(select(m.GovernanceSetting).order_by(m.GovernanceSetting.key))
    return {"items": [{"key": r.key, "value": r.value, "description": r.description, "updated_at": iso(r.updated_at),
                       "updated_by": r.updated_by, "lock_version": r.lock_version} for r in rows]}


@router.put("/admin/settings/{key}")
def put_setting(key: str, payload: SettingUpdate, ctx: RequestContext = Depends(get_ctx)):
    require_global_role(ctx.principal, Role.ADMIN, action="Changing governance configuration")
    before = settings.get(ctx.session, key) if ctx.session.get(m.GovernanceSetting, key) else None
    row = settings.update(ctx.session, key, payload.value, payload.expected_lock_version, ctx.principal.user_id,
                          ctx.now)
    from app.core.digests import digest
    audit.record(ctx, action="settings.updated", object_type="governance_setting", object_id=key,
                 object_revision=row.lock_version, before_digest=digest(before) if before else None,
                 after_digest=digest(row.value), details={"before": before, "after": row.value})
    ctx.commit()
    return {"key": row.key, "value": row.value, "lock_version": row.lock_version}


@router.get("/admin/users")
def list_users(ctx: RequestContext = Depends(get_ctx)):
    require_global_role(ctx.principal, Role.ADMIN, action="Viewing access")
    out = []
    for u in ctx.session.scalars(select(m.User).order_by(m.User.username)):
        out.append({"id": u.id, "username": u.username, "display_name": u.display_name, "team": u.team,
                    "is_active": u.is_active,
                    "role_assignments": [{"id": r.id, "role": r.role, "scope_id": r.scope_id}
                                         for r in u.role_assignments]})
    return {"items": out}


@router.post("/admin/role-assignments", status_code=201)
def grant_role(payload: RoleGrant, ctx: RequestContext = Depends(get_ctx)):
    require_global_role(ctx.principal, Role.ADMIN, action="Managing application access")
    if payload.user_id == ctx.principal.user_id:
        raise Forbidden("Administrators cannot change their own role assignments.", code="SELF_GRANT")
    if ctx.session.get(m.User, payload.user_id) is None:
        raise NotFound("User not found")
    if payload.scope_id:
        ctx.tree.get(payload.scope_id)
    exists = ctx.session.scalars(select(m.RoleAssignment).where(
        m.RoleAssignment.user_id == payload.user_id, m.RoleAssignment.role == payload.role,
        m.RoleAssignment.scope_id.is_(None) if payload.scope_id is None
        else m.RoleAssignment.scope_id == payload.scope_id)).first()
    if exists:
        raise Conflict("Role assignment already exists.")
    ra = m.RoleAssignment(id=new_id("ra"), user_id=payload.user_id, role=payload.role, scope_id=payload.scope_id,
                          created_at=ctx.now, created_by=ctx.principal.user_id)
    ctx.session.add(ra)
    ctx.session.flush()
    audit.record(ctx, action="access.role_granted", object_type="user", object_id=payload.user_id,
                 scope_ids=[payload.scope_id] if payload.scope_id else [],
                 details={"role": payload.role, "scope_id": payload.scope_id})
    ctx.commit()
    return {"id": ra.id, "user_id": ra.user_id, "role": ra.role, "scope_id": ra.scope_id}


@router.delete("/admin/role-assignments/{assignment_id}")
def revoke_role(assignment_id: str, ctx: RequestContext = Depends(get_ctx)):
    require_global_role(ctx.principal, Role.ADMIN, action="Managing application access")
    ra = ctx.session.get(m.RoleAssignment, assignment_id)
    if ra is None:
        raise NotFound("Role assignment not found")
    if ra.user_id == ctx.principal.user_id:
        raise Forbidden("Administrators cannot change their own role assignments.", code="SELF_GRANT")
    ctx.session.delete(ra)
    audit.record(ctx, action="access.role_revoked", object_type="user", object_id=ra.user_id,
                 details={"role": ra.role, "scope_id": ra.scope_id})
    ctx.commit()
    return {"revoked": assignment_id}
