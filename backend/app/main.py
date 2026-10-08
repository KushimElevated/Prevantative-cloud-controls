"""FastAPI application factory (modular monolith)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    routes_admin,
    routes_auth,
    routes_controls,
    routes_exceptions,
    routes_inventory,
    routes_rollouts,
    routes_workspace,
)
from app.api.middleware import RequestGuardMiddleware
from app.core import db
from app.core.clock import Clock, build_clock
from app.core.config import Settings, validate_settings
from app.core.context import current_correlation_id
from app.core.errors import DomainError
from app.services import audit

log = logging.getLogger("ccp")

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _record_denial(request: Request, exc: DomainError) -> None:
    """Authorization and gate denials on commands are audited in their own transaction, because the
    command transaction itself is rolled back."""
    principal = getattr(request.state, "principal", None)
    if request.method not in UNSAFE or exc.status_code not in (403, 409) or principal is None:
        return
    if db._session_factory is None:  # noqa: SLF001
        return
    session = db._session_factory()  # noqa: SLF001
    try:
        audit.record_raw(session, principal=principal, now=request.app.state.clock.now(),
                         action="command.denied", object_type="request", object_id=request.url.path[:128],
                         reason=exc.message[:2000], details={"code": exc.code, "method": request.method})
        session.commit()
    except Exception:  # pragma: no cover - denial auditing must never mask the original error
        session.rollback()
        log.exception("failed to audit denial")
    finally:
        session.close()


def create_app(settings: Settings | None = None, clock: Clock | None = None) -> FastAPI:
    settings = validate_settings(settings or Settings())
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db.init_engine(settings.database_url)
    app = FastAPI(
        title="Cloud Security Control Engineering Platform (local MVP)",
        version="0.1.0",
        description="Security intent -> governed, verifiable preventive controls. Fixture data only; no cloud "
                    "mutations. Delivery is a local GitOps handoff bundle and a clearly labelled mock pipeline.",
        openapi_url="/api/openapi.json", docs_url="/api/docs", redoc_url=None,
    )
    app.state.settings = settings
    app.state.clock = clock or build_clock(settings.app_clock_fixed)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["authorization", "content-type", "x-correlation-id"],
                       expose_headers=["x-correlation-id"])
    app.add_middleware(RequestGuardMiddleware, max_bytes=settings.max_request_bytes)

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError):
        _record_denial(request, exc)
        return JSONResponse(status_code=exc.status_code, content={"error": {
            "code": exc.code, "message": exc.message, "details": exc.details,
            "correlation_id": current_correlation_id()}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {
            "code": "REQUEST_INVALID", "message": "Request validation failed", "details": details,
            "correlation_id": current_correlation_id()}})

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        cid = getattr(request.state, "correlation_id", None) or current_correlation_id()
        log.exception("unhandled error cid=%s", cid)
        return JSONResponse(status_code=500, headers={"x-correlation-id": cid}, content={"error": {
            "code": "INTERNAL_ERROR", "message": "Internal error. Quote the correlation id when reporting.",
            "correlation_id": cid}})

    @app.get("/api/health", tags=["health"])
    def health():
        with db.get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "mode": settings.auth_mode, "environment": settings.app_env,
                "live_deployment": False, "handoff_adapter": settings.handoff_adapter,
                "pipeline_mode": settings.pipeline_mode}

    # Optional AI provider override (tests/offline demos only; never set from configuration).
    app.state.workspace_ai_provider = None
    for r in (routes_auth, routes_controls, routes_inventory, routes_exceptions, routes_rollouts, routes_admin,
              routes_workspace):
        app.include_router(r.router, prefix="/api/v1")
    return app
