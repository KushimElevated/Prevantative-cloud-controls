"""Experience preferences, feature discovery and the optional AI Control Workspace.

``/features`` and ``/me/preferences`` are always available. Everything under ``/workspace`` returns 404
unless ENABLE_A2UI_WORKSPACE=true, so the Classic Experience is unaffected when the flag is off.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field, StringConstraints, ValidationError

from app.api.deps import get_ctx
from app.api.schemas import Strict
from app.core.errors import NotFound, ValidationFailed
from app.models import entities as m
from app.services import audit
from app.services.context import RequestContext
from app.workspace import drafts
from app.workspace.ai.orchestrator import ai_status, run_ai_investigation
from app.workspace.catalog import catalog_document
from app.workspace.composer import compose
from app.workspace.intents import EXAMPLE_QUESTIONS, INTENTS, interpret
from app.workspace.tools import Investigation, run_tool, tool_catalog

router = APIRouter(tags=["workspace"])

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
EXPERIENCES = ("classic", "workspace")


def _enabled(request: Request) -> bool:
    return bool(request.app.state.settings.enable_a2ui_workspace)


def require_workspace(request: Request) -> None:
    if not _enabled(request):
        raise NotFound("The AI Control Workspace is not enabled.")


def _override(request: Request):
    return getattr(request.app.state, "workspace_ai_provider", None)


class PreferencesUpdate(Strict):
    experience: Literal["classic", "workspace"]
    workspace_mode: Literal["deterministic", "ai"] = "deterministic"


class InvestigationRequest(Strict):
    question: str = Field(min_length=3, max_length=500)
    mode: Literal["deterministic", "ai"] = "deterministic"
    intent: Literal["impact_preview", "exception_review", "rollout_status", "readiness", "coverage_status",
                    "audit_history", "control_overview"] | None = None
    control_id: Id | None = None
    scope_id: Id | None = None


class DraftPrepare(Strict):
    kind: Literal["exception", "rollout_plan", "control"]
    params: dict[str, Any]


def _prefs_view(request: Request, user: m.User) -> dict[str, Any]:
    prefs = user.preferences or {}
    exp = prefs.get("experience") if prefs.get("experience") in EXPERIENCES else "classic"
    mode = prefs.get("workspace_mode") if prefs.get("workspace_mode") in ("deterministic", "ai") else "deterministic"
    return {"experience": exp, "effective_experience": exp if _enabled(request) else "classic",
            "workspace_mode": mode}


@router.get("/features")
def features(request: Request, ctx: RequestContext = Depends(get_ctx)):
    enabled = _enabled(request)
    return {"a2ui_workspace": {"enabled": enabled},
            "workspace_ai": ai_status(request.app.state.settings, _override(request)) if enabled else
            {"available": False, "reason": "The AI Control Workspace is disabled."}}


@router.get("/me/preferences")
def get_preferences(request: Request, ctx: RequestContext = Depends(get_ctx)):
    return _prefs_view(request, ctx.session.get(m.User, ctx.principal.user_id))


@router.put("/me/preferences")
def put_preferences(payload: PreferencesUpdate, request: Request, ctx: RequestContext = Depends(get_ctx)):
    if payload.experience == "workspace" and not _enabled(request):
        raise ValidationFailed("The AI Control Workspace is not enabled; the Classic Experience is the only option.")
    user = ctx.session.get(m.User, ctx.principal.user_id)
    before = dict(user.preferences or {})
    user.preferences = {**before, "experience": payload.experience, "workspace_mode": payload.workspace_mode}
    audit.record(ctx, action="user.preferences_updated", object_type="user", object_id=user.id,
                 details={"before": before, "after": user.preferences})
    ctx.commit()
    return _prefs_view(request, user)


@router.get("/workspace/catalog", dependencies=[Depends(require_workspace)])
def workspace_catalog(ctx: RequestContext = Depends(get_ctx)):
    return catalog_document()


@router.get("/workspace/tools", dependencies=[Depends(require_workspace)])
def workspace_tools(ctx: RequestContext = Depends(get_ctx)):
    return {"items": tool_catalog(),
            "note": "READ tools run automatically within your authorised scopes. DRAFT tools return unsaved "
                    "proposals. COMMAND tools are executed only by the existing governed endpoints after you confirm; "
                    "they are never offered to a model."}


@router.get("/workspace/intents", dependencies=[Depends(require_workspace)])
def workspace_intents(ctx: RequestContext = Depends(get_ctx)):
    return {"intents": [{"id": k, "label": v["label"], "views": v["views"]} for k, v in INTENTS.items()],
            "examples": EXAMPLE_QUESTIONS}


@router.post("/workspace/investigations", dependencies=[Depends(require_workspace)])
def investigate(payload: InvestigationRequest, request: Request, ctx: RequestContext = Depends(get_ctx)):
    inv = Investigation(ctx)
    interpretation = interpret(inv, payload.question, control_id=payload.control_id, scope_id=payload.scope_id,
                               intent=payload.intent)
    if payload.mode == "ai":
        result = run_ai_investigation(inv, payload.question, interpretation, request.app.state.settings,
                                      _override(request))
    else:
        result = compose(inv, payload.question, interpretation)
        result["mode"] = "deterministic"
        result["mode_note"] = None
    result["requested_mode"] = payload.mode
    return result


@router.post("/workspace/drafts/prepare", dependencies=[Depends(require_workspace)])
def prepare_draft(payload: DraftPrepare, ctx: RequestContext = Depends(get_ctx)):
    tool = {"exception": "prepare_exception_draft", "rollout_plan": "prepare_rollout_draft",
            "control": "prepare_control_draft"}[payload.kind]
    try:
        return run_tool(Investigation(ctx), tool, payload.params, allowed_kinds={"DRAFT"})
    except ValidationError as exc:
        raise ValidationFailed("Draft parameters failed validation.",
                               details=[{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]) from exc


@router.post("/workspace/drafts/submit", status_code=201, dependencies=[Depends(require_workspace)])
def submit_draft(payload: drafts.DraftSubmit, ctx: RequestContext = Depends(get_ctx)):
    out = drafts.submit_draft(Investigation(ctx), payload)
    ctx.commit()
    return out
