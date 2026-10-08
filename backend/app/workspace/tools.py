"""Typed workspace tools.

READ tools run automatically (deterministic mode and AI-assisted mode) with the requesting principal's
authorization: every row is filtered with the same readable-scope rules the Classic API uses, and every
value comes from an existing service or persisted record. DRAFT tools return unsaved proposals.
COMMAND tools are never executed by the workspace server or offered to a model: the user's browser
calls the existing governed endpoint after explicit confirmation.

Tool outputs double as the A2UI data-model values (see ``catalog.py``), so the canvas shows exactly
what the tools returned.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated, Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from app.core.clock import iso
from app.core.errors import NotFound
from app.models import entities as m
from app.models.enums import (
    STAGE_ORDER,
    Applicability,
    AssessmentStatus,
    ConfigurationResult,
    ExceptionDisposition,
    Readiness,
    RequestImpact,
    Role,
    RolloutStage,
)
from app.providers.common import is_stale
from app.providers.registry import get_provider
from app.services import audit as audit_service
from app.services import settings
from app.services.context import RequestContext
from app.services.control_detail import control_detail
from app.services.dashboard import resource_coverage
from app.services.exceptions import decisions_for, exception_view
from app.services.implementations import latest_validation
from app.services.rollouts import latest_package, package_decisions
from app.services.views import audit_view, evidence_view

Id = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
ResourceId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9/][A-Za-z0-9._:/@-]{0,511}$")]
MAX_ROWS = 200


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchControlsInput(ToolInput):
    query: str | None = Field(default=None, max_length=300, description="Free-text words to match.")
    provider: Literal["azure", "aws"] | None = Field(default=None, description="Cloud provider filter.")
    resource_type: str | None = Field(default=None, max_length=128,
                                      description="Native resource type, e.g. Microsoft.Search/searchServices.")


class ControlInput(ToolInput):
    control_id: Id = Field(description="Control id, e.g. CTL-AZ-SEARCH-PNA.")


class ControlScopeInput(ToolInput):
    control_id: Id = Field(description="Control id, e.g. CTL-AZ-SEARCH-PNA.")
    scope_id: Id | None = Field(default=None, description="Scope id to focus on; null for the widest readable.")


class ResourcesInput(ControlScopeInput):
    configuration_result: Literal["COMPLIANT", "NON_COMPLIANT", "UNKNOWN", "NOT_APPLICABLE"] | None = Field(
        default=None, description="Optional configuration result filter.")


class AuditInput(ToolInput):
    control_id: Id = Field(description="Control id.")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum events (1-50).")


class RunAssessmentInput(ToolInput):
    implementation_revision_id: Id
    target_scope_id: Id


class ExceptionDraftInput(ToolInput):
    control_id: Id
    resource_id: ResourceId


class RolloutDraftInput(ToolInput):
    control_id: Id
    implementation_revision_id: Id | None = None


class ControlDraftInput(ToolInput):
    problem_statement: str = Field(min_length=10, max_length=1000)
    provider: Literal["azure", "aws"] | None = None
    resource_type: str | None = Field(default=None, max_length=128)


# ------------------------------------------------------------------------------------ shared helpers


@dataclass
class Investigation:
    """Per-request memo so the same run/control is not loaded repeatedly."""

    ctx: RequestContext
    memo: dict[str, Any] = field(default_factory=dict)

    def cached(self, key: str, fn: Callable[[], Any]) -> Any:
        if key not in self.memo:
            self.memo[key] = fn()
        return self.memo[key]

    @property
    def readable(self) -> set[str] | None:
        return self.cached("readable", lambda: self.ctx.principal.readable_scope_ids(self.ctx.tree))

    def can_read(self, scope_id: str | None) -> bool:
        return scope_id is not None and (self.readable is None or scope_id in self.readable)

    def detail(self, control_id: str) -> dict[str, Any]:
        return self.cached(f"detail:{control_id}", lambda: control_detail(self.ctx, control_id))

    def control(self, control_id: str) -> m.Control:
        c = self.ctx.session.get(m.Control, control_id)
        if c is None:
            raise NotFound(f"Control {control_id} not found")
        return c


def _link(label: str, href: str) -> dict[str, str]:
    return {"label": label[:120], "href": href}


def _within(inv: Investigation, scope_id: str | None, ancestor: str | None) -> bool:
    if scope_id is None:
        return False
    if ancestor is None:
        return True
    return inv.ctx.tree.is_within(scope_id, ancestor)


def _check_scope(inv: Investigation, scope_id: str | None) -> str | None:
    if scope_id is None:
        return None
    if scope_id not in inv.ctx.tree.scopes or not inv.can_read(scope_id):
        raise NotFound(f"Scope {scope_id} not found")
    return scope_id


def select_run(inv: Investigation, control_id: str, scope_id: str | None) -> m.AssessmentRun | None:
    """Latest completed run whose target scope covers the requested scope (or the latest at all)."""
    def pick() -> m.AssessmentRun | None:
        runs = inv.ctx.session.scalars(select(m.AssessmentRun).where(
            m.AssessmentRun.control_id == control_id, m.AssessmentRun.status == AssessmentStatus.COMPLETED)
            .order_by(m.AssessmentRun.seq.desc()))
        for run in runs:
            if scope_id is None or _within(inv, scope_id, run.target_scope_id):
                return run
        return None
    return inv.cached(f"run:{control_id}:{scope_id}", pick)


def _result_rows(inv: Investigation, run: m.AssessmentRun, scope_id: str | None, kind: str) -> list[m.AssessmentResult]:
    def load() -> list[m.AssessmentResult]:
        rows = inv.ctx.session.scalars(select(m.AssessmentResult).where(
            m.AssessmentResult.run_id == run.id, m.AssessmentResult.subject_kind == kind)
            .order_by(m.AssessmentResult.subject_id))
        return [r for r in rows if inv.can_read(r.scope_id) and _within(inv, r.scope_id, scope_id)]
    return inv.cached(f"rows:{run.id}:{scope_id}:{kind}", load)


def _run_is_filtered(inv: Investigation, run: m.AssessmentRun, scope_id: str | None) -> bool:
    return (scope_id is not None and scope_id != run.target_scope_id) or not inv.can_read(run.target_scope_id)


def _latest_impl_revision(control: m.Control, provider: str | None = None) -> m.ImplementationRevision | None:
    for impl in control.implementations:
        if provider is None or impl.provider == provider:
            if impl.revisions:
                return impl.revisions[-1]
    return None


# ------------------------------------------------------------------------------------ READ tools


STOPWORDS = {"the", "and", "for", "all", "any", "are", "was", "were", "what", "which", "who", "whom", "why", "how",
             "would", "should", "could", "will", "can", "does", "did", "has", "have", "had", "with", "without",
             "from", "into", "this", "that", "these", "those", "there", "our", "your", "their", "about", "happen",
             "happens", "prevent", "prevented", "prevents", "control", "controls", "show", "list", "tell", "give",
             "need", "want", "ready", "make", "made", "use", "using", "than", "then", "when", "where", "they",
             "them", "its", "not", "out", "get"}


def _words(text: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2 and w not in STOPWORDS}


def search_controls(inv: Investigation, inp: SearchControlsInput) -> dict[str, Any]:
    q = _words(inp.query)
    items = []
    for c in inv.ctx.session.scalars(select(m.Control).order_by(m.Control.id)):
        if not c.revisions:
            continue
        rev = c.revisions[-1]
        if inp.provider and inp.provider not in rev.providers:
            continue
        reasons, score = [], 0
        if inp.resource_type:
            if any(rt.lower() == inp.resource_type.lower() for rt in rev.resource_types):
                score += 10
                reasons.append(f"resource type {inp.resource_type}")
            else:
                continue
        text = _words(" ".join([c.id, rev.name, rev.description, rev.security_objective,
                                " ".join(rev.resource_types)]))
        overlap = sorted(q & text)
        score += len(overlap)
        if overlap:
            reasons.append("matches: " + ", ".join(overlap[:8]))
        if q and not overlap and not inp.resource_type:
            continue
        items.append({"control_id": c.id, "name": rev.name, "status": rev.status, "revision": rev.revision,
                      "providers": rev.providers, "resource_types": rev.resource_types, "origin": c.origin,
                      "operational_owner": c.operational_owner, "score": score, "match_reasons": reasons})
    items.sort(key=lambda i: (-i["score"], i["control_id"]))
    return {"items": items[:20], "total": len(items),
            "note": "Catalogue entries are readable by every role; scoped evidence is filtered per identity."}


def get_control_details(inv: Investigation, inp: ControlInput) -> dict[str, Any]:
    d = inv.detail(inp.control_id)
    rev = d["current_revision"]
    impls = []
    for info in d["implementations"]:
        latest = info["revisions"][-1]
        val = info["latest_validation"]
        impls.append({
            "id": info["implementation"]["id"], "name": info["implementation"]["name"],
            "provider": info["implementation"]["provider"],
            "mechanism_role": info["implementation"]["mechanism_role"],
            "latest_revision_id": latest["id"], "latest_revision": latest["revision"], "status": latest["status"],
            "validation_outcome": val["outcome"] if val else None,
            "integration_ready": val["integration_ready"] if val else None,
            "verification_status": (latest.get("verification") or {}).get("status"),
            "source_ref": latest["source_ref"], "pinned_version": latest["pinned_version"],
        })
    nd = d.get("next_decision")
    return {
        "control_id": d["control"]["id"], "name": rev["name"], "revision": rev["revision"], "revision_id": rev["id"],
        "status": rev["status"], "severity": rev["severity"], "origin": d["control"]["origin"],
        "operational_owner": d["control"]["operational_owner"], "providers": rev["providers"],
        "resource_types": rev["resource_types"], "security_objective": rev["security_objective"],
        "prevention_boundary": rev["prevention_boundary"], "limitations": rev["limitations"],
        "exception_eligible": rev["exception_eligible"], "security_owner": rev["security_owner"],
        "engineering_owner": rev["engineering_owner"], "implementations": impls,
        "next_decision": {"decision": nd["decision"], "owner_role": nd["owner_role"], "detail": nd["detail"]}
        if nd else None,
        "links": [_link("Open control in Classic", f"/controls/{d['control']['id']}")]
        + [_link(f"Implementation {i['name']}", f"/implementations/{i['id']}") for i in impls],
    }


def get_implementations(inv: Investigation, inp: ControlInput) -> dict[str, Any]:
    d = inv.detail(inp.control_id)
    out = []
    for info in d["implementations"]:
        latest = info["revisions"][-1]
        val = info["latest_validation"]
        caps = info["capabilities"]
        out.append({
            "implementation_id": info["implementation"]["id"], "name": info["implementation"]["name"],
            "provider": info["implementation"]["provider"], "management": info["implementation"]["management"],
            "latest_revision_id": latest["id"], "status": latest["status"], "policy_kind": latest["policy_kind"],
            "source_ref": latest["source_ref"], "pinned_version": latest["pinned_version"],
            "content_digest": latest["content_digest"], "parameters": latest["parameters"],
            "verification": latest.get("verification"),
            "validation": {"outcome": val["outcome"], "evaluator_id": val["evaluator_id"],
                           "integration_ready": val["integration_ready"],
                           "integration_blockers": val["integration_blockers"]} if val else None,
            "capabilities": {k: caps.get(k) for k in ("supported_effects", "native_audit_mode",
                                                      "native_expiry_supported", "exemption_mechanism",
                                                      "supported_scope_types", "integration_status")},
            "prerequisites": [p.get("id") for p in info["prerequisites"]],
        })
    return {"control_id": inp.control_id, "implementations": out}


COVERAGE_COLUMNS = [
    ("applicable", "Applicable"), ("compliant", "Compliant"), ("non_compliant", "Non-compliant"),
    ("unknown", "Unknown config"), ("effective_exemptions", "Effective exemptions"),
    ("verified_protected", "Verified protected"), ("unknown_enforcement", "Unknown enforcement"),
    ("not_enforced", "Not enforced"),
]


def get_current_coverage(inv: Investigation, inp: ControlScopeInput) -> dict[str, Any]:
    scope_id = _check_scope(inv, inp.scope_id)
    inv.control(inp.control_id)
    run = select_run(inv, inp.control_id, scope_id)
    base = {"unit": "resources (one control)", "columns": [{"key": k, "label": label} for k, label in COVERAGE_COLUMNS],
            "denominator": "Known-applicable resources in the latest completed assessment covering this scope; "
                           "exceptions stay in the denominator.",
            "links": [_link("Dashboard", "/dashboard")]}
    if run is None:
        return {**base, "available": False, "assessment_run_id": None, "rows": [], "totals": {},
                "verified_protected_ratio": "N/A",
                "unavailable_reason": "No completed assessment covers this scope, so coverage is unknown."}
    cov = inv.cached(f"cov:{run.id}", lambda: resource_coverage(inv.ctx, inp.control_id, run))
    states = {p["resource_id"]: p["state"] for p in cov["per_resource"]}
    tree = inv.ctx.tree
    groups: dict[str, Counter] = {}
    for r in _result_rows(inv, run, scope_id, "RESOURCE"):
        key = tree.account_or_subscription(r.scope_id) or r.scope_id
        c = groups.setdefault(key, Counter())
        if r.applicability != Applicability.APPLICABLE:
            continue
        c["applicable"] += 1
        c[{ConfigurationResult.COMPLIANT: "compliant", ConfigurationResult.NON_COMPLIANT: "non_compliant",
           ConfigurationResult.UNKNOWN: "unknown"}.get(r.configuration_result, "other")] += 1
        state = states.get(r.subject_id)
        c[{"EFFECTIVE_EXEMPTION": "effective_exemptions", "VERIFIED_PROTECTED": "verified_protected",
           "UNKNOWN_ENFORCEMENT": "unknown_enforcement", "NOT_ENFORCED": "not_enforced"}.get(state or "", "other")] += 1
    rows, totals = [], Counter()
    for sid in sorted(groups):
        s = tree.scopes.get(sid)
        cells = {k: groups[sid].get(k, 0) for k, _ in COVERAGE_COLUMNS}
        totals.update(cells)
        rows.append({"scope_id": sid, "scope_name": s.display_name if s else sid,
                     "environment": s.environment if s else None, "cells": cells})
    den = totals.get("applicable", 0)
    return {**base, "available": True, "unavailable_reason": None, "assessment_run_id": run.id, "rows": rows,
            "totals": {k: totals.get(k, 0) for k, _ in COVERAGE_COLUMNS},
            "verified_protected_ratio": "N/A" if den == 0 else f"{totals.get('verified_protected', 0)}/{den}",
            "links": base["links"] + [_link("Assessment run", f"/assessments/{run.id}")]}


def _basis(inv: Investigation, control: m.Control, run: m.AssessmentRun) -> tuple[bool, list[str]]:
    notes = []
    current_crev = control.revisions[-1]
    if run.control_revision_id != current_crev.id:
        notes.append(f"Assessment used control revision {run.control_revision_id}; the current revision is "
                     f"r{current_crev.revision} ({current_crev.status}).")
    irev = inv.ctx.session.get(m.ImplementationRevision, run.implementation_revision_id)
    if irev is not None:
        latest = irev.implementation.revisions[-1]
        if latest.id != irev.id:
            notes.append(f"Assessment used implementation revision r{irev.revision}; the latest is r{latest.revision}.")
    fresh = settings.get(inv.ctx.session, "evidence_freshness")
    if is_stale(run.assessed_at, inv.ctx.now, timedelta(hours=fresh["assessment_max_age_hours"])):
        notes.append(f"Assessment is older than {fresh['assessment_max_age_hours']} hours.")
    snap = inv.ctx.session.get(m.InventorySnapshot, run.inventory_snapshot_id)
    if snap is not None and is_stale(snap.collected_at, inv.ctx.now, timedelta(hours=fresh["inventory_max_age_hours"])):
        notes.append("Inventory snapshot is older than the freshness threshold.")
    return not notes, notes


def _can_run(inv: Investigation, control: m.Control, scope_id: str | None) -> tuple[bool, str | None, dict | None]:
    if not inv.ctx.principal.has_role(Role.CONTROL_ENGINEER):
        return False, "Running an assessment requires the CONTROL_ENGINEER role.", None
    if scope_id is None:
        return False, "Select a scope first.", None
    scope = inv.ctx.tree.get(scope_id)
    irev = _latest_impl_revision(control, scope.provider)
    if irev is None:
        return False, f"No {scope.provider} implementation exists for this control.", None
    caps = get_provider(scope.provider).capabilities()
    if scope.scope_type not in caps.supported_scope_types:
        return False, f"Assessments are not supported at scope type {scope.scope_type}.", None
    return True, None, {"implementation_revision_id": irev.id, "target_scope_id": scope_id}


def get_impact_assessment(inv: Investigation, inp: ControlScopeInput) -> dict[str, Any]:
    scope_id = _check_scope(inv, inp.scope_id)
    control = inv.control(inp.control_id)
    run = select_run(inv, control.id, scope_id)
    can_run, blocked, action = _can_run(inv, control, scope_id)
    base = {"requested_scope_id": scope_id, "can_run": can_run, "run_blocked_reason": blocked,
            "run_action": action, "links": []}
    if run is None:
        return {**base, "available": False,
                "unavailable_reason": "No completed impact assessment covers this scope. Impact is UNKNOWN until "
                                      "an assessment runs; the workspace does not estimate it."}
    filtered = _run_is_filtered(inv, run, scope_id)
    basis_current, notes = _basis(inv, control, run)
    snap = inv.ctx.session.get(m.InventorySnapshot, run.inventory_snapshot_id)
    out = {**base, "available": True, "unavailable_reason": None, "run_id": run.id, "status": run.status,
           "target_scope_id": run.target_scope_id, "assessed_at": iso(run.assessed_at),
           "evaluator": f"{run.evaluator_id} v{run.evaluator_version}" if run.evaluator_id else None,
           "basis_current": basis_current, "basis_notes": notes, "filtered_to_scope": filtered,
           "data_provenance": snap.provenance if snap else None,
           "links": [_link("Open assessment in Classic", f"/assessments/{run.id}")]}
    res = _result_rows(inv, run, scope_id, "RESOURCE")
    reqs = _result_rows(inv, run, scope_id, "REQUEST")
    applicable = [r for r in res if r.applicability == Applicability.APPLICABLE]
    if not filtered:
        roll = run.rollup
        out.update({
            "configuration": roll["configuration"]["counts"],
            "total_resources": roll["configuration"]["total_resources_evaluated"],
            "reconciles": roll["configuration"]["reconciles"],
            "exceptions": roll["exceptions"]["counts"],
            "request_impact": {"evidence_present": roll["request_impact"]["evidence_present"],
                               "total": roll["request_impact"]["total_requests_evaluated"],
                               "counts": roll["request_impact"]["counts"], "note": roll["request_impact"]["note"]},
            "readiness": roll["readiness"]["resource_counts"],
            "newly_preventive_resources": roll["baseline_vs_proposed"]["newly_preventive_resources"],
            "confidence": {k: {"category": v["category"], "reasons": v["reasons"]}
                           for k, v in run.confidence.items() if isinstance(v, dict) and "category" in v},
            "disclosure": run.disclosure,
        })
        return out
    # Counts restricted to the requested (or readable) scope, from the run's persisted results only.
    cfg = Counter(r.configuration_result for r in res)
    exc = Counter(r.exception_disposition for r in applicable)
    rdy = Counter(r.readiness or "NOT_EVALUATED" for r in res)
    imp = Counter(r.request_impact for r in reqs)
    has_requests = run.request_fixture_set_id is not None
    out.update({
        "filter_note": (f"Counts are the persisted results of run {run.id} (target {run.target_scope_id}) "
                        f"restricted to {scope_id or 'the scopes you can read'}"
                        + ("" if inv.can_read(run.target_scope_id) else "; results outside your scopes are hidden")
                        + ". No re-evaluation was performed."),
        "configuration": {k.value: cfg.get(k.value, 0) for k in ConfigurationResult},
        "total_resources": len(res), "reconciles": sum(cfg.values()) == len(res),
        "exceptions": {k.value: exc.get(k.value, 0) for k in ExceptionDisposition},
        "request_impact": {
            "evidence_present": has_requests, "total": len(reqs),
            "counts": {k.value: imp.get(k.value, 0) for k in RequestImpact} if has_requests else None,
            "note": ("Covers only supplied representative requests within this scope." if has_requests else
                     "No request evidence was supplied. Potentially blocked operations are UNKNOWN.")},
        "readiness": {k: rdy.get(k, 0) for k in ["READY", "BLOCKED", "UNKNOWN", "NOT_EVALUATED"]},
        "newly_preventive_resources": sum(1 for r in applicable if (r.baseline or {}).get("summary") in (
            "NONE", "AUDIT_ONLY", "PARTIAL_ACTION_COVERAGE")),
        "confidence": ({k: {"category": v["category"], "reasons": v["reasons"]}
                        for k, v in run.confidence.items() if isinstance(v, dict) and "category" in v}
                       if inv.can_read(run.target_scope_id) else None),
        "disclosure": run.disclosure if inv.can_read(run.target_scope_id) else None,
    })
    return out


def get_applicable_resources(inv: Investigation, inp: ResourcesInput) -> dict[str, Any]:
    scope_id = _check_scope(inv, inp.scope_id)
    control = inv.control(inp.control_id)
    run = select_run(inv, control.id, scope_id)
    if run is None:
        return {"available": False, "control_id": control.id, "run_id": None, "scope_id": scope_id, "total": 0,
                "truncated": False, "rows": [], "links": []}
    rows = _result_rows(inv, run, scope_id, "RESOURCE")
    if inp.configuration_result:
        rows = [r for r in rows if r.configuration_result == inp.configuration_result]
    eligible = control.revisions[-1].exception_eligible
    out = []
    for r in rows[:MAX_ROWS]:
        can_exc = bool(eligible and r.applicability == Applicability.APPLICABLE
                       and r.configuration_result != ConfigurationResult.COMPLIANT and r.scope_id
                       and inv.ctx.principal.has_role_at(Role.EXCEPTION_REQUESTER, r.scope_id, inv.ctx.tree))
        out.append({
            "resource_id": r.subject_id, "name": r.subject_name, "scope_id": r.scope_id,
            "resource_type": r.resource_type, "application": r.application, "owner": r.owner,
            "applicability": r.applicability, "configuration_result": r.configuration_result,
            "exception_disposition": r.exception_disposition, "exception_id": r.exception_id,
            "readiness": r.readiness, "reasons": [str(x)[:500] for x in (r.reasons or [])[:4]],
            "missing_fields": list(r.missing_fields or []), "can_prepare_exception": can_exc,
        })
    return {"available": True, "control_id": control.id, "run_id": run.id, "scope_id": scope_id, "total": len(rows),
            "truncated": len(rows) > MAX_ROWS, "rows": out,
            "links": [_link("All results in Classic", f"/assessments/{run.id}")]}


def get_application_readiness(inv: Investigation, inp: ControlScopeInput) -> dict[str, Any]:
    scope_id = _check_scope(inv, inp.scope_id)
    control = inv.control(inp.control_id)
    run = select_run(inv, control.id, scope_id)
    fresh = settings.get(inv.ctx.session, "evidence_freshness")
    max_age = timedelta(days=fresh["readiness_max_age_days"])
    ev_rows = inv.ctx.session.scalars(select(m.ReadinessEvidence).where(
        m.ReadinessEvidence.control_id == control.id).order_by(m.ReadinessEvidence.collected_at.desc()))
    evidence = []
    for e in ev_rows:
        if not inv.can_read(e.scope_id) or not (_within(inv, e.scope_id, scope_id) or _within(inv, scope_id, e.scope_id)):
            continue
        v = evidence_view(e)
        evidence.append({"id": v["id"], "application": v["application"], "scope_id": v["scope_id"],
                         "prerequisite": v["prerequisite"], "status": v["status"], "collected_at": v["collected_at"],
                         "stale": is_stale(e.collected_at, inv.ctx.now, max_age), "provided_by": v["provided_by"],
                         "provenance": v["provenance"]})
    note = ("Readiness is derived from supplied evidence (attested, not tested). Stale evidence older than "
            f"{fresh['readiness_max_age_days']} days does not count.")
    if run is None:
        return {"available": False, "run_id": None, "applications": [], "evidence": evidence[:50],
                "note": note + " No assessment covers this scope, so readiness per resource is UNKNOWN."}
    by_app: dict[str, dict[str, Any]] = {}
    for r in _result_rows(inv, run, scope_id, "RESOURCE"):
        if r.readiness is None:
            continue
        app = r.application or "(unknown application)"
        entry = by_app.setdefault(app, {"states": [], "resources": 0})
        entry["states"].append(r.readiness)
        entry["resources"] += 1
    apps = []
    for app, entry in sorted(by_app.items()):
        states = entry["states"]
        readiness = (Readiness.BLOCKED if Readiness.BLOCKED in states else
                     Readiness.UNKNOWN if Readiness.UNKNOWN in states else Readiness.READY)
        blockers = [{"kind": b["kind"], "message": b["message"], "resolution": b["resolution"],
                     "owner_team": b["owner_team"], "scope_id": b.get("scope_id")}
                    for b in run.blockers
                    if (b.get("application") or "(unknown application)") == app and inv.can_read(b.get("scope_id"))
                    and _within(inv, b.get("scope_id"), scope_id)]
        apps.append({"application": app, "readiness": str(readiness), "resources": entry["resources"],
                     "blockers": blockers})
    return {"available": True, "run_id": run.id, "applications": apps, "evidence": evidence[:50], "note": note}


def get_exceptions(inv: Investigation, inp: ControlScopeInput) -> dict[str, Any]:
    scope_id = _check_scope(inv, inp.scope_id)
    inv.control(inp.control_id)
    warn = settings.get(inv.ctx.session, "expiry")["warning_days"]
    items = []
    rows = inv.ctx.session.scalars(select(m.SecurityException).where(
        m.SecurityException.control_id == inp.control_id).order_by(m.SecurityException.expires_at,
                                                                     m.SecurityException.id))
    for e in rows:
        if not inv.can_read(e.scope_id):
            continue
        if scope_id and not (_within(inv, e.scope_id, scope_id) or _within(inv, scope_id, e.scope_id)):
            continue
        v = exception_view(e, inv.ctx.now, warn, decisions_for(inv.ctx, e.id))
        items.append({"id": v["id"], "scope_id": v["scope_id"], "application": v["application"],
                      "granularity": v["granularity"], "resource_ids": v["resource_ids"],
                      "effective_status": v["effective_status"], "native_status": v["native_status"],
                      "disposition": v["disposition"], "representability": v["representability"],
                      "expires_at": v["expires_at"], "days_until_expiry": v["days_until_expiry"],
                      "expiring_soon": v["expiring_soon"], "requester_id": v["requester_id"],
                      "href": f"/exceptions/{v['id']}"})
    return {"items": items, "counts_by_status": dict(Counter(i["effective_status"] for i in items)),
            "note": "Governance status (decision) and native status (exemption in the cloud) are separate. "
                    "An approved exception does not make a failing configuration compliant.",
            "links": [_link("Exceptions in Classic", f"/exceptions?control_id={inp.control_id}")]}


def _settings_summary(settings_: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(settings_.items()))[:500] or "-"


def get_rollout_status(inv: Investigation, inp: ControlInput) -> dict[str, Any]:
    control = inv.control(inp.control_id)
    plans = [p for p in inv.ctx.session.scalars(select(m.RolloutPlan).where(
        m.RolloutPlan.control_id == control.id).order_by(m.RolloutPlan.created_at.desc()))
        if inv.can_read(p.target_scope_id)]
    plan = next((p for p in plans if p.state != "CANCELLED"), None)
    if plan is None:
        timeline = {"plan": None, "suggested": False, "stages": [
            {"stage": s.value, "status": "NOT_PLANNED", "scope_ids": [], "settings_summary": "-"}
            for s in STAGE_ORDER if s != RolloutStage.ASSESSMENT],
            "note": "No active rollout plan. A rollout draft can be prepared (unsaved) and submitted through the "
                    "governed plan workflow."}
        approval = {"package": None, "required_roles": ["SECURITY_APPROVER", "CLOUD_ENGINEER"], "decisions": [],
                    "failing_gates": [], "note": "No change package exists yet. Approvals happen only in the Classic "
                                                 "Experience by two different identities; the workspace never approves."}
        handoff = {"bundle": None, "package_status": None,
                   "note": "No handoff bundle. Bundles are exported only from approved change packages; approved or "
                           "exported does not mean deployed."}
        return {"plan_id": None, "timeline": timeline, "approval": approval, "handoff": handoff}
    current = STAGE_ORDER.index(RolloutStage(plan.stage))
    ring_by_stage = {r["stage"]: r for r in plan.rings}
    stages = []
    for s in STAGE_ORDER:
        if s == RolloutStage.ASSESSMENT:
            continue
        ring = ring_by_stage.get(s.value)
        idx = STAGE_ORDER.index(s)
        status = ("NOT_PLANNED" if ring is None else "DONE" if idx < current else
                  "CURRENT" if idx == current else "PLANNED")
        stages.append({"stage": s.value, "status": status, "scope_ids": ring["scope_ids"] if ring else [],
                       "settings_summary": _settings_summary(ring["settings"]) if ring else "-"})
    timeline = {"plan": {"id": plan.id, "title": plan.title, "stage": plan.stage, "state": plan.state,
                         "target_scope_id": plan.target_scope_id, "href": f"/controls/{control.id}"},
                "suggested": False, "stages": stages,
                "note": f"Plan stage {plan.stage} ({plan.state}). Progression is a Cloud Engineering command in the "
                        "Classic Experience."}
    pkg = latest_package(inv.ctx, plan.id)
    if pkg is None:
        approval = {"package": None, "required_roles": ["SECURITY_APPROVER", "CLOUD_ENGINEER"], "decisions": [],
                    "failing_gates": [], "note": "Plan exists but no change package has been submitted."}
        handoff = {"bundle": None, "package_status": None, "note": "No package, so no handoff bundle."}
    else:
        decisions = [{"role": d.role, "actor_id": d.actor_id, "decision": d.decision, "decided_at": iso(d.decided_at),
                      "bound_to_current_digest": d.subject_digest == pkg.manifest_digest}
                     for d in package_decisions(inv.ctx, pkg)]
        failing = [{"id": g["id"], "label": g.get("label", g["id"]), "phase": g.get("phase", ""),
                    "detail": json.dumps(g.get("detail"), default=str)[:1000]}
                   for g in (pkg.gate_results or []) if g.get("status") != "PASS"]
        approval = {"package": {"id": pkg.id, "revision": pkg.package_revision, "status": pkg.status,
                                "manifest_digest": pkg.manifest_digest, "through_stage": pkg.through_stage,
                                "href": f"/controls/{control.id}"},
                    "required_roles": ["SECURITY_APPROVER", "CLOUD_ENGINEER"], "decisions": decisions,
                    "failing_gates": failing,
                    "note": "Gate results as recorded at the last evaluation. Decisions count only when bound to the "
                            "current manifest digest. Approve or reject in the Classic Experience."}
        bundle = inv.ctx.session.scalars(select(m.HandoffBundle).where(m.HandoffBundle.package_id == pkg.id)
                                         .order_by(m.HandoffBundle.exported_at.desc())).first()
        if bundle is None:
            handoff = {"bundle": None, "package_status": pkg.status,
                       "note": "No bundle exported for the latest package. Export happens in the Classic Experience "
                               "and re-runs every gate."}
        else:
            try:
                banner = json.loads(bundle.files.get("manifest.json", "{}")).get("status_banner", bundle.kind)
            except (ValueError, AttributeError):
                banner = bundle.kind
            handoff = {"bundle": {"id": bundle.id, "kind": bundle.kind, "banner": str(banner)[:500],
                                  "bundle_digest": bundle.bundle_digest, "exported_at": iso(bundle.exported_at),
                                  "files": [{"path": p, "sha256": d} for p, d in sorted(bundle.file_digests.items())],
                                  "href": f"/bundles/{bundle.id}"},
                       "package_status": pkg.status,
                       "note": "Approved or exported does not mean deployed. Cloud Engineering's pipeline delivers; "
                               "only fresh observations verify."}
    return {"plan_id": plan.id, "timeline": timeline, "approval": approval, "handoff": handoff}


def get_audit_history(inv: Investigation, inp: AuditInput) -> dict[str, Any]:
    inv.control(inp.control_id)
    rows, total = audit_service.query_events(inv.ctx, {"control_id": inp.control_id}, limit=inp.limit, offset=0)
    return {"total": total, "items": [{k: v for k, v in audit_view(a).items()
                                       if k in ("occurred_at", "actor_id", "action", "object_type", "object_id",
                                                "object_revision", "reason")} for a in rows]}


# ------------------------------------------------------------------------------------ composer-only views


def policy_diff(inv: Investigation, control_id: str, scope_id: str | None) -> dict[str, Any]:
    control = inv.control(control_id)
    d = inv.detail(control_id)
    provider = inv.ctx.tree.get(scope_id).provider if scope_id else (control.revisions[-1].providers or [None])[0]
    irev = _latest_impl_revision(control, provider)
    baseline = []
    for b in d["existing_bindings"]:
        if scope_id and not (_within(inv, scope_id, b["target_scope_id"]) or _within(inv, b["target_scope_id"], scope_id)):
            continue
        if irev is not None and b["definition_ref"].lower() != irev.source_ref.lower() and b["provider"] == "azure":
            continue
        s = b["settings"] or {}
        baseline.append({"binding_id": b["id"], "native_id": b["native_id"], "scope_id": b["target_scope_id"],
                         "effect": s.get("effect"), "enforcement_mode": s.get("enforcementMode"),
                         "management": b["management"], "origin": b["origin"], "observed_at": b["observed_at"],
                         "provenance": b["evidence_provenance"]})
    proposed, changes = None, []
    if irev is not None:
        val = latest_validation(inv.ctx, irev)
        proposed = {"implementation_revision_id": irev.id, "policy_kind": irev.policy_kind,
                    "source_ref": irev.source_ref, "pinned_version": irev.pinned_version,
                    "content_digest": irev.content_digest, "parameters": irev.parameters,
                    "assignment_settings": irev.assignment_settings,
                    "verification": (irev.verification or {}).get("status", "UNKNOWN")
                    + ("" if val is None else f" / validation {val.outcome}")}
        p_effect = (irev.parameters or {}).get("effect")
        p_mode = (irev.assignment_settings or {}).get("enforcementMode")
        for b in baseline:
            if p_effect is not None and b["effect"] is not None and str(p_effect) != str(b["effect"]):
                changes.append({"field": "effect", "scope_id": b["scope_id"], "baseline": str(b["effect"]),
                                "proposed": str(p_effect)})
            if p_mode is not None and b["enforcement_mode"] is not None and str(p_mode) != str(b["enforcement_mode"]):
                changes.append({"field": "enforcementMode", "scope_id": b["scope_id"],
                                "baseline": str(b["enforcement_mode"]), "proposed": str(p_mode)})
        if not baseline:
            changes.append({"field": "assignment", "scope_id": scope_id, "baseline": "none recorded",
                            "proposed": f"{irev.policy_kind} {irev.source_ref.rsplit('/', 1)[-1]}"})
    run = select_run(inv, control_id, scope_id)
    counts: dict[str, int] = {}
    if run is not None:
        counts = dict(Counter((r.baseline or {}).get("summary", "NONE")
                              for r in _result_rows(inv, run, scope_id, "RESOURCE")
                              if r.applicability == Applicability.APPLICABLE))
    return {"baseline": baseline, "proposed": proposed, "changes": changes, "baseline_coverage_counts": counts,
            "note": "Baseline is the recorded native bindings (desired/observed, with provenance). The proposal is "
                    "the latest implementation revision; rings may override effect per stage. Nothing is deployed "
                    "from this view."}


def scope_options(inv: Investigation, provider: str | None, selected: str | None) -> dict[str, Any]:
    tree = inv.ctx.tree
    options = []
    for sid in sorted(tree.scopes, key=lambda s: [p["id"] for p in tree.path(s)]):
        s = tree.scopes[sid]
        if provider and s.provider != provider:
            continue
        if not inv.can_read(sid):
            continue
        options.append({"scope_id": sid, "name": s.display_name, "scope_type": s.scope_type,
                        "environment": s.environment, "depth": len(tree.ancestors(sid)) - 1})
    return {"provider": provider, "selected_scope_id": selected, "options": options,
            "note": "Only scopes your identity can read are listed. Changing scope re-runs the read-only "
                    "investigation; it does not change any data."}


def evidence_panel(inv: Investigation, control_id: str, scope_id: str | None) -> dict[str, Any]:
    run = select_run(inv, control_id, scope_id)
    fresh = settings.get(inv.ctx.session, "evidence_freshness")
    sources: list[dict[str, Any]] = []
    demo = True
    confidence = None
    disclosure = None
    limitations: list[str] = []
    assumptions: list[str] = []
    if run is not None:
        snap = inv.ctx.session.get(m.InventorySnapshot, run.inventory_snapshot_id)
        if snap is not None:
            demo = snap.provenance != "LIVE"
            sources.append({"kind": "inventory_snapshot", "id": snap.id, "label": snap.label,
                            "provenance": snap.provenance, "collected_at": iso(snap.collected_at),
                            "digest": snap.content_digest,
                            "stale": is_stale(snap.collected_at, inv.ctx.now,
                                              timedelta(hours=fresh["inventory_max_age_hours"]))})
        if run.request_fixture_set_id:
            fs = inv.ctx.session.get(m.RequestFixtureSet, run.request_fixture_set_id)
            if fs is not None:
                sources.append({"kind": "request_fixtures", "id": fs.id, "label": fs.label,
                                "provenance": fs.provenance, "collected_at": None, "digest": fs.content_digest,
                                "stale": None})
        sources.append({"kind": "assessment_run", "id": run.id,
                        "label": f"{run.evaluator_id} v{run.evaluator_version}", "provenance": "PERSISTED",
                        "collected_at": iso(run.assessed_at), "digest": run.result_digest,
                        "stale": is_stale(run.assessed_at, inv.ctx.now,
                                          timedelta(hours=fresh["assessment_max_age_hours"]))})
        if inv.can_read(run.target_scope_id):
            confidence = {k: {"category": v["category"], "reasons": v["reasons"]}
                          for k, v in run.confidence.items() if isinstance(v, dict) and "category" in v}
            disclosure = run.disclosure
            limitations = list(run.limitations)
            assumptions = list(run.assumptions)
        else:
            limitations = ["Run-level disclosure, confidence and limitations are hidden because the assessment "
                           "target includes scopes you cannot read."]
    d = inv.detail(control_id)
    for b in d["existing_bindings"]:
        if scope_id and not (_within(inv, scope_id, b["target_scope_id"]) or _within(inv, b["target_scope_id"], scope_id)):
            continue
        sources.append({"kind": "policy_binding", "id": b["id"], "label": b["native_id"].rsplit("/", 1)[-1],
                        "provenance": b["evidence_provenance"], "collected_at": b["observed_at"], "digest": None,
                        "stale": None})
    return {"demo_data": demo, "sources": sources[:40], "confidence": confidence, "disclosure": disclosure,
            "limitations": limitations, "assumptions": assumptions}


# ------------------------------------------------------------------------------------ registry


@dataclass(frozen=True)
class ToolDef:
    name: str
    api_name: str
    kind: Literal["READ", "DRAFT", "COMMAND"]
    description: str
    input_model: type[ToolInput]
    handler: Callable[[Investigation, Any], dict[str, Any]] | None
    executed_by: str


def _drafts():
    from app.workspace import drafts  # local import: drafts depend on these tools
    return drafts


TOOLS: list[ToolDef] = [
    ToolDef("SearchControls", "search_controls", "READ",
            "Search the control catalogue by words, provider and native resource type.", SearchControlsInput,
            search_controls, "workspace, read-only"),
    ToolDef("GetControlDetails", "get_control_details", "READ",
            "Security intent, current revision status, implementations and the next required decision.",
            ControlInput, get_control_details, "workspace, read-only"),
    ToolDef("GetImplementations", "get_implementations", "READ",
            "Implementation revisions with verification, validation and capabilities.", ControlInput,
            get_implementations, "workspace, read-only"),
    ToolDef("GetCurrentCoverage", "get_current_coverage", "READ",
            "Per-scope coverage from the latest completed assessment covering the scope.", ControlScopeInput,
            get_current_coverage, "workspace, read-only"),
    ToolDef("GetImpactAssessment", "get_impact_assessment", "READ",
            "The persisted impact assessment covering the scope (counts restricted to the scope).",
            ControlScopeInput, get_impact_assessment, "workspace, read-only"),
    ToolDef("RunImpactAssessment", "run_impact_assessment", "COMMAND",
            "Run a new impact assessment (persisted and audited).", RunAssessmentInput, None,
            "Classic API POST /api/v1/assessments, from the user's browser after explicit confirmation; "
            "requires CONTROL_ENGINEER. Never offered to a model."),
    ToolDef("GetApplicableResources", "get_applicable_resources", "READ",
            "Per-resource results of the persisted assessment within the scope.", ResourcesInput,
            get_applicable_resources, "workspace, read-only"),
    ToolDef("GetApplicationReadiness", "get_application_readiness", "READ",
            "Readiness by application, open blockers and supplied readiness evidence.", ControlScopeInput,
            get_application_readiness, "workspace, read-only"),
    ToolDef("GetExceptions", "get_exceptions", "READ",
            "Exceptions for the control with governance status, native status, disposition and expiry.",
            ControlScopeInput, get_exceptions, "workspace, read-only"),
    ToolDef("GetRolloutStatus", "get_rollout_status", "READ",
            "Active rollout plan, latest change package, digest-bound decisions, failing gates and handoff bundle.",
            ControlInput, get_rollout_status, "workspace, read-only"),
    ToolDef("GetAuditHistory", "get_audit_history", "READ",
            "Recent audit events for the control (filtered to the scopes you can read).", AuditInput,
            get_audit_history, "workspace, read-only"),
    ToolDef("PrepareControlDraft", "prepare_control_draft", "DRAFT",
            "Prepare an unsaved control proposal, or point to an existing control that already covers it.",
            ControlDraftInput, lambda inv, inp: _drafts().prepare_control_draft(inv, inp),
            "workspace (unsaved); submitted only via /api/v1/workspace/drafts/submit after confirmation, which "
            "calls the existing control service"),
    ToolDef("PrepareExceptionDraft", "prepare_exception_draft", "DRAFT",
            "Prepare an unsaved exception request for one resource, with a representability preview.",
            ExceptionDraftInput, lambda inv, inp: _drafts().prepare_exception_draft(inv, inp),
            "workspace (unsaved); submitted only via /api/v1/workspace/drafts/submit after confirmation, which "
            "calls the existing exception service"),
    ToolDef("PrepareRolloutDraft", "prepare_rollout_draft", "DRAFT",
            "Prepare an unsaved rollout plan from the provider template, bound to the current assessment basis.",
            RolloutDraftInput, lambda inv, inp: _drafts().prepare_rollout_draft(inv, inp),
            "workspace (unsaved); submitted only via /api/v1/workspace/drafts/submit after confirmation, which "
            "calls the existing rollout service"),
]
TOOLS_BY_API_NAME = {t.api_name: t for t in TOOLS}
TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def run_tool(inv: Investigation, api_name: str, raw_input: dict[str, Any], *, allowed_kinds: set[str]) -> dict[str, Any]:
    """Validate input against the tool's schema and run it with the investigation's principal."""
    tool = TOOLS_BY_API_NAME.get(api_name)
    if tool is None or tool.kind not in allowed_kinds or tool.handler is None:
        raise PermissionError(f"Tool {api_name!r} is not available here")
    inp = tool.input_model.model_validate(raw_input)
    return tool.handler(inv, inp)


def _strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic JSON schema -> strict-tool-compatible schema (constraints are enforced server-side)."""
    drop = {"title", "default", "minLength", "maxLength", "pattern", "minimum", "maximum", "examples"}

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: clean(v) for k, v in node.items() if k not in drop}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = sorted(out["properties"])
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    return clean(schema)


def tool_specs(kinds: set[str]) -> list[dict[str, Any]]:
    return [{"name": t.api_name, "description": t.description,
             "input_schema": _strict_schema(t.input_model.model_json_schema())}
            for t in TOOLS if t.kind in kinds and t.handler is not None]


def tool_catalog() -> list[dict[str, Any]]:
    return [{"name": t.name, "api_name": t.api_name, "kind": t.kind, "description": t.description,
             "executed_by": t.executed_by, "input_schema": t.input_model.model_json_schema()} for t in TOOLS]
