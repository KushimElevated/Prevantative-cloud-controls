"""Compose an investigation: run READ tools, derive labelled findings, build and validate the A2UI surface.

Findings are generated from tool outputs only and carry a kind:

* ``EVIDENCE``   - established from persisted records (assessment results, bindings, exceptions, plans).
* ``ESTIMATE``   - predictions bounded by supplied fixtures (representative requests, baseline deltas).
* ``UNKNOWN``    - what the platform cannot know from the available evidence.
* ``RECOMMENDATION`` - rule-based next steps (``origin: platform``) or model suggestions (``origin: ai``).
"""

from __future__ import annotations

import secrets
from typing import Any, Callable

from app.core.clock import iso
from app.core.errors import DomainError
from app.models.enums import Role
from app.workspace import tools as t
from app.workspace.a2ui import build_surface, validate_messages
from app.workspace.catalog import CATALOG_ID, DOMAIN_COMPONENTS, PROTOCOL_VERSION
from app.workspace.intents import INTENTS, Interpretation

TITLES = {
    "ControlSummary": "Control and security intent",
    "ScopeSelector": "Scope",
    "ImpactAssessment": "Impact assessment",
    "ResourceImpactTable": "Affected resources",
    "ApplicationReadinessPanel": "Application readiness",
    "ExceptionReview": "Exceptions",
    "PolicyDiffViewer": "Existing coverage vs proposed policy",
    "ControlCoverageMatrix": "Coverage by subscription / account",
    "RolloutTimeline": "Rollout",
    "ApprovalStatus": "Change package approvals",
    "GitOpsHandoffPreview": "GitOps handoff",
    "EvidencePanel": "Evidence and provenance",
}
VIEW_KEYS = {
    "ControlSummary": "control", "ScopeSelector": "scope", "ImpactAssessment": "impact",
    "ResourceImpactTable": "resources", "ApplicationReadinessPanel": "readiness", "ExceptionReview": "exceptions",
    "PolicyDiffViewer": "policy_diff", "ControlCoverageMatrix": "coverage", "RolloutTimeline": "rollout",
    "ApprovalStatus": "approval", "GitOpsHandoffPreview": "handoff", "EvidencePanel": "evidence",
}
TOOL_FOR_VIEW = {
    "ControlSummary": "GetControlDetails", "ImpactAssessment": "GetImpactAssessment",
    "ResourceImpactTable": "GetApplicableResources", "ApplicationReadinessPanel": "GetApplicationReadiness",
    "ExceptionReview": "GetExceptions", "PolicyDiffViewer": "GetCurrentCoverage",
    "ControlCoverageMatrix": "GetCurrentCoverage", "RolloutTimeline": "GetRolloutStatus",
    "ApprovalStatus": "GetRolloutStatus", "GitOpsHandoffPreview": "GetRolloutStatus",
    "EvidencePanel": "GetImpactAssessment", "ScopeSelector": "(scope list)",
}


def _finding(kind: str, text: str, source_type: str | None = None, source_id: str | None = None,
             href: str | None = None, origin: str = "platform") -> dict[str, Any]:
    return {"kind": kind, "text": text, "origin": origin,
            "source": {"type": source_type, "id": source_id, "href": href} if source_type else None}


def _count_text(counts: dict[str, int], labels: dict[str, str]) -> str:
    return ", ".join(f"{counts.get(k, 0)} {v}" for k, v in labels.items())


class Composer:
    def __init__(self, inv: t.Investigation, interpretation: Interpretation):
        self.inv = inv
        self.it = interpretation
        self.steps: list[dict[str, Any]] = []
        self.views: dict[str, Any] = {}
        self.findings: list[dict[str, Any]] = []

    # ------------------------------------------------------------------------------ tool execution
    def _step(self, tool: str, label: str, fn: Callable[[], Any]) -> Any:
        try:
            out = fn()
        except DomainError as exc:
            self.steps.append({"tool": tool, "label": label, "status": "error", "detail": exc.message[:300]})
            return None
        self.steps.append({"tool": tool, "label": label, "status": "ok", "detail": None})
        return out

    def build_views(self, components: list[str]) -> None:
        cid, sid, inv = self.it.control_id, self.it.scope_id, self.inv
        if cid is None:
            return
        scope_inp = t.ControlScopeInput(control_id=cid, scope_id=sid)
        needed = set(components) | {"ControlSummary", "ImpactAssessment"}
        if "ControlSummary" in needed:
            self.views["control"] = self._step("GetControlDetails", f"Read control {cid}",
                                               lambda: t.get_control_details(inv, t.ControlInput(control_id=cid)))
            self._step("GetImplementations", "Read implementations and validation",
                       lambda: t.get_implementations(inv, t.ControlInput(control_id=cid)))
        if "ImpactAssessment" in needed:
            self.views["impact"] = self._step(
                "GetImpactAssessment", f"Find the persisted assessment covering {sid or 'any scope'}",
                lambda: t.get_impact_assessment(inv, scope_inp))
        if "ResourceImpactTable" in needed:
            self.views["resources"] = self._step(
                "GetApplicableResources", "List per-resource results",
                lambda: t.get_applicable_resources(inv, t.ResourcesInput(control_id=cid, scope_id=sid)))
        if "ApplicationReadinessPanel" in needed:
            self.views["readiness"] = self._step("GetApplicationReadiness", "Summarise readiness by application",
                                                 lambda: t.get_application_readiness(inv, scope_inp))
        if "ExceptionReview" in needed:
            self.views["exceptions"] = self._step("GetExceptions", "List exceptions in scope",
                                                  lambda: t.get_exceptions(inv, scope_inp))
        if needed & {"ControlCoverageMatrix", "PolicyDiffViewer"}:
            self.views["coverage"] = self._step("GetCurrentCoverage", "Compute coverage from persisted results",
                                                lambda: t.get_current_coverage(inv, scope_inp))
            self.views["policy_diff"] = self._step("GetCurrentCoverage", "Compare existing bindings with the proposal",
                                                   lambda: t.policy_diff(inv, cid, sid))
        if needed & {"RolloutTimeline", "ApprovalStatus", "GitOpsHandoffPreview"}:
            rollout = self._step("GetRolloutStatus", "Read rollout plan, package and handoff",
                                 lambda: t.get_rollout_status(inv, t.ControlInput(control_id=cid)))
            if rollout:
                self.views["rollout"] = rollout["timeline"]
                self.views["approval"] = rollout["approval"]
                self.views["handoff"] = rollout["handoff"]
        if "EvidencePanel" in needed:
            self.views["evidence"] = self._step("GetImpactAssessment", "Collect provenance and confidence",
                                                lambda: t.evidence_panel(inv, cid, sid))
        if "ScopeSelector" in needed:
            self.views["scope"] = t.scope_options(inv, self.it.provider, sid)

    # ------------------------------------------------------------------------------ findings
    def derive_findings(self) -> None:
        f = self.findings
        cid, sid = self.it.control_id, self.it.scope_id
        if cid is None:
            f.append(_finding("UNKNOWN", "No control in the catalogue matches this question, so there is nothing to "
                                         "assess yet. A control proposal can be drafted for review."))
            return
        ctl = self.views.get("control")
        if ctl:
            f.append(_finding("EVIDENCE", f"{ctl['control_id']} r{ctl['revision']} ({ctl['status']}): {ctl['name']}. "
                                          f"Prevention boundary: {ctl['prevention_boundary']}",
                              "control_revision", ctl["revision_id"], f"/controls/{ctl['control_id']}"))
            for impl in ctl["implementations"]:
                val = impl["validation_outcome"] or "not validated"
                f.append(_finding("EVIDENCE", f"Implementation {impl['name']} ({impl['provider']}, r"
                                              f"{impl['latest_revision']} {impl['status']}): validation {val}; "
                                              f"verification {impl['verification_status'] or 'unknown'}.",
                                  "implementation", impl["id"], f"/implementations/{impl['id']}"))
        imp = self.views.get("impact")
        if imp and imp["available"]:
            src = ("assessment_run", imp["run_id"], f"/assessments/{imp['run_id']}")
            demo = " (demo fixture inventory)" if imp.get("data_provenance") == "FIXTURE" else ""
            cfg = imp["configuration"] or {}
            where = sid or imp["target_scope_id"]
            f.append(_finding("EVIDENCE", f"Assessment {imp['run_id']} evaluated {imp['total_resources']} resource(s) "
                                          f"in {where}{demo}: " + _count_text(cfg, {
                                              "COMPLIANT": "compliant", "NON_COMPLIANT": "non-compliant",
                                              "UNKNOWN": "unknown configuration",
                                              "NOT_APPLICABLE": "not applicable"}) + ".", *src))
            if imp.get("filtered_to_scope") and imp.get("filter_note"):
                f.append(_finding("EVIDENCE", imp["filter_note"], *src))
            exc = imp.get("exceptions") or {}
            if any(exc.get(k) for k in ("EFFECTIVE", "PENDING", "APPROVED_UNAPPLIED", "EXPIRED", "UNSUPPORTED")):
                f.append(_finding("EVIDENCE", "Exceptions among applicable resources: " + _count_text(exc, {
                    "EFFECTIVE": "effective", "APPROVED_UNAPPLIED": "approved but not applied", "PENDING": "pending",
                    "EXPIRED": "expired", "UNSUPPORTED": "not representable"})
                    + ". An exception does not make a failing configuration compliant.", *src))
            req = imp.get("request_impact")
            if req and req["evidence_present"] and req["counts"] is not None:
                f.append(_finding("ESTIMATE", f"Of {req['total']} supplied representative request(s), "
                                  + _count_text(req["counts"], {"PREDICTED_DENIED": "would be denied",
                                                                "NOT_DENIED_BY_THIS_CONTROL": "not denied by this control",
                                                                "UNKNOWN": "unknown"})
                                  + ". Requests that were not supplied are not predicted.", *src))
            else:
                f.append(_finding("UNKNOWN", "No representative request evidence covers this scope: potentially "
                                             "blocked operations are UNKNOWN (not zero).", *src))
            if imp.get("newly_preventive_resources") is not None:
                f.append(_finding("ESTIMATE", f"{imp['newly_preventive_resources']} applicable resource(s) have no "
                                              "preventive coverage today (none, audit-only or partial) and would be "
                                              "prevented once the control is enforced and verified.", *src))
            if cfg.get("UNKNOWN"):
                f.append(_finding("UNKNOWN", f"{cfg['UNKNOWN']} resource(s) have unknown configuration (missing "
                                             "fields in the inventory); their outcome cannot be predicted.", *src))
            rdy = imp.get("readiness") or {}
            if rdy.get("BLOCKED") or rdy.get("UNKNOWN"):
                f.append(_finding("UNKNOWN" if rdy.get("UNKNOWN") else "EVIDENCE",
                                  f"Readiness: {rdy.get('READY', 0)} ready, {rdy.get('BLOCKED', 0)} blocked, "
                                  f"{rdy.get('UNKNOWN', 0)} unknown (evidence is attested, not tested).", *src))
            for note in imp.get("basis_notes") or []:
                f.append(_finding("UNKNOWN", f"Assessment basis caveat: {note}", *src))
        elif imp:
            f.append(_finding("UNKNOWN", imp["unavailable_reason"] or "No assessment.", "scope", sid))
            if imp.get("can_run"):
                f.append(_finding("RECOMMENDATION", f"Run the existing impact assessment at {sid} (persisted and "
                                                    "audited; you will be asked to confirm).", "scope", sid))
            elif imp.get("run_blocked_reason"):
                f.append(_finding("RECOMMENDATION", f"Ask a control engineer to run the impact assessment at "
                                                    f"{sid}. ({imp['run_blocked_reason']})", "scope", sid))
        ex = self.views.get("exceptions")
        if ex and ex["items"] and not (imp and imp["available"]):
            f.append(_finding("EVIDENCE", "Exceptions recorded for this control in scope: " + ", ".join(
                f"{n} {s.lower()}" for s, n in sorted(ex["counts_by_status"].items())) + ".", "control", cid,
                f"/exceptions?control_id={cid}"))
        rd = self.views.get("readiness")
        if rd and rd["available"]:
            for app in rd["applications"]:
                if app["readiness"] == "BLOCKED" and app["blockers"]:
                    b = app["blockers"][0]
                    f.append(_finding("EVIDENCE", f"{app['application']} is BLOCKED: {b['message']} "
                                                  f"(owner: {b['owner_team']}).", "assessment_run", rd["run_id"],
                                      f"/assessments/{rd['run_id']}"))
        ro = self.views.get("rollout")
        if ro is not None:
            if ro["plan"]:
                f.append(_finding("EVIDENCE", f"Rollout plan {ro['plan']['id']} is at stage {ro['plan']['stage']} "
                                              f"({ro['plan']['state']}).", "rollout_plan", ro["plan"]["id"],
                                  ro["plan"]["href"]))
            else:
                f.append(_finding("EVIDENCE", "No active rollout plan exists for this control.", "control", cid))
        ap = self.views.get("approval")
        if ap and ap["package"]:
            p = ap["package"]
            approved = sorted({d["role"] for d in ap["decisions"] if d["decision"] == "APPROVE"
                               and d["bound_to_current_digest"]})
            f.append(_finding("EVIDENCE", f"Change package r{p['revision']} is {p['status']}; approvals bound to the "
                                          f"current digest: {', '.join(approved) or 'none'}.", "change_package",
                              p["id"], p["href"]))
        nd = (ctl or {}).get("next_decision")
        if nd and nd["decision"] != "No decision pending":
            f.append(_finding("RECOMMENDATION", f"Next required decision: {nd['decision']} (owner: "
                                                f"{nd['owner_role']}). {nd['detail']}".strip(), "control", cid,
                              f"/controls/{cid}"))
        res = self.views.get("resources")
        if res and any(r["can_prepare_exception"] for r in res["rows"]):
            f.append(_finding("RECOMMENDATION", "For a resource that cannot comply before enforcement, prepare an "
                                                "exception draft from the resource table; a security approver "
                                                "decides it.", "control", cid))

    # ------------------------------------------------------------------------------ context panel
    def context(self) -> dict[str, Any]:
        inv, cid, sid = self.inv, self.it.control_id, self.it.scope_id
        p = inv.ctx.principal
        tree = inv.ctx.tree
        ctl = self.views.get("control")
        imp = self.views.get("impact") or {}
        actions: list[dict[str, Any]] = []
        if cid is not None:
            actions.append({
                "id": "run_assessment", "kind": "command",
                "label": f"Run impact assessment at {sid}" if sid else "Run impact assessment",
                "enabled": bool(imp.get("can_run")), "reason": imp.get("run_blocked_reason"),
                "params": imp.get("run_action"),
                "confirm": "Creates a persisted, audited assessment run through the existing assessment service. "
                           "It reads fixtures only and never changes infrastructure. Approved change packages that "
                           "reference an older assessment will need re-approval.",
            })
            irev = next((i["latest_revision_id"] for i in (ctl or {}).get("implementations", [])
                         if i["provider"] == self.it.provider), None)
            actions.append({
                "id": "prepare_rollout_draft", "kind": "draft", "label": "Prepare rollout plan draft",
                "enabled": p.has_role(Role.CONTROL_ENGINEER) and irev is not None,
                "reason": None if p.has_role(Role.CONTROL_ENGINEER) else "Requires CONTROL_ENGINEER.",
                "params": {"control_id": cid, "implementation_revision_id": irev}, "confirm": None,
            })
        else:
            actions.append({
                "id": "prepare_control_draft", "kind": "draft", "label": "Prepare control proposal draft",
                "enabled": p.has_role(Role.CONTROL_ENGINEER),
                "reason": None if p.has_role(Role.CONTROL_ENGINEER) else "Requires CONTROL_ENGINEER.",
                "params": {"provider": self.it.provider, "resource_type": self.it.resource_type}, "confirm": None,
            })
        links = []
        if cid:
            links.append(t._link("Control detail (Classic)", f"/controls/{cid}"))
            if imp.get("run_id"):
                links.append(t._link("Assessment (Classic)", f"/assessments/{imp['run_id']}"))
            links.append(t._link("Exceptions (Classic)", f"/exceptions?control_id={cid}"))
            links.append(t._link("Rollouts (Classic)", "/rollouts"))
            links.append(t._link("Audit history (Classic)", f"/audit?control_id={cid}"))
        else:
            links.append(t._link("Control catalogue (Classic)", "/controls"))
        demo = (self.views.get("evidence") or {}).get("demo_data", True)
        scope = tree.scopes.get(sid) if sid else None
        return {
            "control": {"id": ctl["control_id"], "name": ctl["name"], "status": ctl["status"],
                        "revision": ctl["revision"]} if ctl else None,
            "scope": {"id": scope.id, "name": scope.display_name, "scope_type": scope.scope_type,
                      "environment": scope.environment, "provider": scope.provider} if scope else None,
            "basis": {"control_revision_id": ctl["revision_id"] if ctl else None,
                      "assessment_run_id": imp.get("run_id")},
            "next_decision": (ctl or {}).get("next_decision"),
            "actions": actions, "links": links,
            "data_labels": (["DEMO DATA: fixture inventory, mock pipeline and fixture observations"] if demo else [])
            + ["Read-only investigation. Changes happen only through governed workflows after you confirm."],
            "identity": {"display_name": p.display_name, "roles": sorted(set(p.roles))},
        }

    # ------------------------------------------------------------------------------ summary and surface
    def summary(self) -> str:
        cid, sid = self.it.control_id, self.it.scope_id
        if cid is None:
            return "No catalogued control matches this question."
        imp = self.views.get("impact")
        if not imp or not imp["available"]:
            return f"{cid} at {sid or 'no readable scope'}: no completed assessment covers this scope, so impact is unknown."
        cfg = imp["configuration"] or {}
        return (f"{cid} at {sid or imp['target_scope_id']}: {cfg.get('NON_COMPLIANT', 0)} non-compliant, "
                f"{cfg.get('COMPLIANT', 0)} compliant and {cfg.get('UNKNOWN', 0)} unknown of "
                f"{imp['total_resources']} evaluated resource(s), from assessment {imp['run_id']}.")

    def surface(self, components: list[str]) -> dict[str, Any]:
        surface_id = f"ws-{secrets.token_hex(6)}"
        sections, notices = [], []
        for comp in components:
            key = VIEW_KEYS[comp]
            if self.views.get(key) is not None:
                sections.append({"component": comp, "title": TITLES[comp], "view": key})
        if self.it.control_id is None:
            notices.append({"tone": "info", "text": "No catalogued control matches this question. Use the actions "
                                                    "panel to prepare a control proposal draft, or rephrase."})
        elif not (self.views.get("impact") or {}).get("available", False):
            notices.append({"tone": "warning", "text": "Impact is UNKNOWN: no completed assessment covers the selected "
                                                       "scope. The workspace never estimates results it has not "
                                                       "evaluated."})
        if (self.views.get("evidence") or {}).get("demo_data"):
            notices.append({"tone": "info", "text": "Demo data: inventory, requests, receipts and observations are "
                                                    "fixtures or mock data."})
        if not sections and not notices:
            notices.append({"tone": "info", "text": "Nothing to show for this question."})
        messages = validate_messages(build_surface(surface_id, sections, self.views, notices))
        return {"protocol_version": PROTOCOL_VERSION, "catalog_id": CATALOG_ID, "surface_id": surface_id,
                "messages": messages}


def compose(inv: t.Investigation, question: str, interpretation: Interpretation,
            components: list[str] | None = None) -> dict[str, Any]:
    comps = [c for c in (components or INTENTS[interpretation.intent]["views"]) if c in DOMAIN_COMPONENTS]
    c = Composer(inv, interpretation)
    c.steps.append({"tool": "interpret", "label": "Interpret the question (deterministic rules)", "status": "ok",
                    "detail": f"intent {interpretation.intent}; matched {interpretation.matched or ['(default)']}"})
    if interpretation.control_id is None:
        c.steps.append({"tool": "SearchControls", "label": "Search the control catalogue", "status": "ok",
                        "detail": "no matching control"})
    c.build_views(comps)
    c.derive_findings()
    return {
        "investigation_id": f"inv-{secrets.token_hex(6)}",
        "question": question,
        "generated_at": iso(inv.ctx.now),
        "intent": interpretation.to_dict(),
        "steps": c.steps,
        "findings": c.findings,
        "summary": {"text": c.summary(), "origin": "platform"},
        "context": c.context(),
        "a2ui": c.surface(comps),
    }
