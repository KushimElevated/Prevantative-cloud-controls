"""Azure Policy provider.

Supports exactly one evaluator: the verified built-in definition
"Azure AI Search services should disable public network access" (ee980b6d-..., v1.0.1).
Implementation artifacts are assignments that REFERENCE the built-in; no equivalent custom
definition is recreated. A changed document, version or kind gets no evaluator.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import iso, parse_utc
from app.core.digests import digest
from app.models.entities import ImplementationRevision, PolicyBinding, ResourceSnapshot, SecurityException
from app.models.enums import (
    Applicability,
    ConfigurationResult,
    ExceptionDisposition,
    ExceptionGranularity,
    Readiness,
    Representability,
    RequestImpact,
    RolloutStage,
    ScopeType,
    VerificationStatus,
)
from app.providers.base import (
    Artifact,
    AssessmentContext,
    Check,
    Comparison,
    ExceptionRepresentation,
    ProviderCapabilities,
    RequestEvaluation,
    ResourceEvaluation,
    ValidationReport,
    outcome_from_checks,
)
from app.providers.common import Blocker, EvidenceView, short_hash
from app.providers.verification import AZURE_SEARCH_PNA, AZURE_VERIFICATION
from app.services.scopes import ScopeTree

POLICY_KIND = "AZURE_POLICY_BUILTIN_ASSIGNMENT"
EVALUATOR_ID = "azure.search.public-network-access"
EVALUATOR_VERSION = "1.0.0"
SEARCH_TYPE = "microsoft.search/searchservices"
PNA_FIELD = "properties.publicNetworkAccess"
ENFORCEMENT_MODES = {"Default", "DoNotEnforce"}

PREREQUISITES = [
    {"id": "PRIVATE_ENDPOINT", "label": "Private endpoint readiness",
     "owner": "Application team with Cloud Engineering",
     "description": "A private endpoint exists (or is planned and approved) for each client path."},
    {"id": "DNS", "label": "DNS readiness", "owner": "Cloud Engineering",
     "description": "privatelink.search.windows.net resolution works from every client network."},
    {"id": "CLIENT_CONNECTIVITY", "label": "Client connectivity validation", "owner": "Application team",
     "description": "Clients were tested against the private path. Supplied evidence only; the platform runs no "
     "network tests."},
    {"id": "APP_OWNER_VALIDATION", "label": "Application-owner validation", "owner": "Application team",
     "description": "The application owner confirms the service operates with public network access disabled."},
]


def format_expires_on(dt: datetime) -> str:
    """Azure exemption expiresOn format yyyy-MM-ddTHH:mm:ss.fffffffZ (UTC)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond:06d}0Z"


def _lower(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


class AzurePolicyProvider:
    name = "azure"

    # ---------------------------------------------------------------- capabilities
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="azure",
            policy_kinds=[POLICY_KIND],
            supported_scope_types=[ScopeType.AZURE_MANAGEMENT_GROUP, ScopeType.AZURE_SUBSCRIPTION,
                                   ScopeType.AZURE_RESOURCE_GROUP],
            supported_effects=list(AZURE_SEARCH_PNA["allowed_effects"]),
            native_audit_mode="Assignment effect 'Audit', or effect 'Deny' with enforcementMode 'DoNotEnforce'. "
            "Definition effect parameter and assignment enforcementMode are separate settings.",
            observation_method="Native compliance evaluation by an Audit/DoNotEnforce assignment (observed here only "
            "through fixture observations) plus offline assessment.",
            validation_methods=[
                "Definition id, version and content digest pinned to a verified built-in copy",
                "Effect parameter checked against the definition's allowedValues",
                "enforcementMode limited to Default/DoNotEnforce (Enroll not modelled)",
                "Deterministic evaluator test vectors",
                "Duplicate/overlap detection by definition id, document digest and scope ancestry",
            ],
            assessment_limitations=[
                "Evaluates only Microsoft.Search/searchServices publicNetworkAccess from the supplied snapshot.",
                "Deny applies to create/update requests; existing violations are reported, not changed.",
                "Public network access setting does not prove data exposure or full network reachability.",
                "PATCH merge semantics are not modelled; such requests without the field are UNKNOWN.",
                "Missing property values are UNKNOWN; provider defaults are never inferred.",
            ],
            exemption_mechanism="Microsoft.Authorization/policyExemptions referencing one assignment (and "
            "policyDefinitionReferenceId for initiative members). Category Waiver or Mitigated.",
            exemption_granularities={
                ExceptionGranularity.SCOPE: "Supported at management group, subscription or resource group "
                "at/under the assignment scope.",
                ExceptionGranularity.RESOURCE: "Supported for individual resources under the assignment scope.",
                ExceptionGranularity.PRINCIPAL: "Not modelled in this MVP (identity-based selectors unverified "
                "for this control).",
            },
            native_expiry_supported=True,
            artifact_kinds=["AZURE_POLICY_ASSIGNMENT", "AZURE_POLICY_EXEMPTION"],
            ring_settings={
                RolloutStage.OBSERVATION: "Reuse an existing Audit assignment (use_existing_binding_id) or assign "
                "with effect Audit / enforcementMode DoNotEnforce.",
                RolloutStage.PILOT: "effect Deny with enforcementMode Default at the pilot scopes.",
                RolloutStage.LIMITED: "effect Deny with enforcementMode Default.",
                RolloutStage.BROAD: "effect Deny with enforcementMode Default.",
            },
            integration_status=AZURE_VERIFICATION["status"],
        )

    def readiness_prerequisites(self) -> list[dict[str, str]]:
        return PREREQUISITES

    # ---------------------------------------------------------------- validation
    def _support_checks(self, revision: ImplementationRevision) -> list[Check]:
        checks: list[Check] = []
        if revision.policy_kind != POLICY_KIND:
            checks.append(Check("policy_kind", "UNSUPPORTED",
                                f"Policy kind {revision.policy_kind} has no Azure evaluator in this MVP."))
            return checks
        checks.append(Check("policy_kind", "PASS", "Built-in policy assignment reference."))
        if revision.source_kind != "BUILT_IN":
            checks.append(Check("source_kind", "UNSUPPORTED",
                                "Custom definitions are not interpreted; only the verified built-in is supported."))
        if (revision.source_ref or "").lower() != AZURE_SEARCH_PNA["definition_id"].lower():
            checks.append(Check("definition_id", "UNSUPPORTED",
                                f"Definition {revision.source_ref} is not a verified, supported built-in."))
        else:
            checks.append(Check("definition_id", "PASS", f"Verified built-in {AZURE_SEARCH_PNA['definition_id']}."))
        if revision.pinned_version != AZURE_SEARCH_PNA["version"]:
            checks.append(Check("pinned_version", "UNSUPPORTED",
                                f"Pinned version {revision.pinned_version!r} differs from verified version "
                                f"{AZURE_SEARCH_PNA['version']}; evaluator is not reused."))
        else:
            checks.append(Check("pinned_version", "PASS", f"Pinned to verified version {revision.pinned_version}."))
        doc = revision.native_document
        if doc is None:
            checks.append(Check("document_digest", "UNSUPPORTED", "No definition copy supplied to pin content."))
        else:
            actual = digest(doc)
            if actual != AZURE_SEARCH_PNA["content_digest"]:
                checks.append(Check("document_digest", "UNSUPPORTED",
                                    "Definition content differs from the verified built-in copy "
                                    f"({actual[:23]}… vs {AZURE_SEARCH_PNA['content_digest'][:23]}…). A changed "
                                    "document never reuses an evaluator."))
            else:
                checks.append(Check("document_digest", "PASS", "Definition content matches verified copy."))
            if revision.content_digest != actual:
                checks.append(Check("content_digest_integrity", "FAIL",
                                    "Recorded content digest does not match the stored document."))
        params = revision.parameters or {}
        if set(params) != {"effect"}:
            checks.append(Check("parameters", "FAIL",
                                f"Parameters must be exactly ['effect']; got {sorted(params)}."))
        elif params["effect"] not in AZURE_SEARCH_PNA["allowed_effects"]:
            checks.append(Check("parameters.effect", "UNSUPPORTED",
                                f"Effect {params['effect']!r} is not in allowedValues "
                                f"{AZURE_SEARCH_PNA['allowed_effects']}."))
        elif params["effect"] == "Disabled":
            checks.append(Check("parameters.effect", "FAIL", "Effect 'Disabled' does not implement the control."))
        else:
            checks.append(Check("parameters.effect", "PASS", f"Effect {params['effect']} is allowed."))
        settings = revision.assignment_settings or {}
        unknown = set(settings) - {"enforcementMode", "notScopes", "nonComplianceMessage"}
        if unknown:
            checks.append(Check("assignment_settings", "FAIL", f"Unknown assignment settings {sorted(unknown)}."))
        mode = settings.get("enforcementMode", "Default")
        if mode not in ENFORCEMENT_MODES:
            checks.append(Check("enforcementMode", "UNSUPPORTED",
                                f"enforcementMode {mode!r} is not modelled (supported: Default, DoNotEnforce)."))
        else:
            checks.append(Check("enforcementMode", "PASS", f"enforcementMode {mode}."))
        not_scopes = settings.get("notScopes", [])
        if not isinstance(not_scopes, list) or not all(isinstance(s, str) for s in not_scopes):
            checks.append(Check("notScopes", "FAIL", "notScopes must be a list of scope ids."))
        return checks

    def evaluator_for(self, revision: ImplementationRevision) -> tuple[str, str] | None:
        checks = self._support_checks(revision)
        if outcome_from_checks(checks) == "PASS":
            return EVALUATOR_ID, EVALUATOR_VERSION
        return None

    def _test_vectors(self, revision: ImplementationRevision) -> list[dict[str, Any]]:
        vectors = [
            ("disabled-exact", {"properties": {"publicNetworkAccess": "Disabled"}}, [], SEARCH_TYPE,
             ConfigurationResult.COMPLIANT),
            ("disabled-lowercase", {"properties": {"publicNetworkAccess": "disabled"}}, [], SEARCH_TYPE,
             ConfigurationResult.COMPLIANT),
            ("enabled", {"properties": {"publicNetworkAccess": "Enabled"}}, [], SEARCH_TYPE,
             ConfigurationResult.NON_COMPLIANT),
            ("field-missing", {"properties": {}}, [PNA_FIELD], SEARCH_TYPE, ConfigurationResult.UNKNOWN),
            ("other-type", {"properties": {"publicNetworkAccess": "Enabled"}}, [],
             "Microsoft.Storage/storageAccounts", ConfigurationResult.NOT_APPLICABLE),
        ]
        results = []
        for name, config, missing, rtype, expected in vectors:
            got = self._evaluate_config(rtype, config, missing).configuration_result
            results.append({"name": name, "expected": expected.value, "actual": got,
                            "passed": got == expected.value})
        return results

    def validate_implementation(self, revision: ImplementationRevision, tree: ScopeTree,
                                bindings: list[PolicyBinding]) -> ValidationReport:
        checks = self._support_checks(revision)
        outcome = outcome_from_checks(checks)
        tests = self._test_vectors(revision) if outcome == "PASS" else []
        if tests and not all(t["passed"] for t in tests):
            checks.append(Check("evaluator_tests", "FAIL", "Evaluator test vectors failed."))
            outcome = "FAIL"
        elif tests:
            checks.append(Check("evaluator_tests", "PASS", f"{len(tests)} evaluator test vectors passed."))
        blockers = []
        if outcome != "PASS":
            blockers.append("Validation did not pass.")
        if revision.verification.get("status") != VerificationStatus.VERIFIED_PRIMARY_SOURCE:
            blockers.append("Provider documentation not verified from a primary source.")
        return ValidationReport(
            outcome=outcome,
            checks=checks,
            evaluator_id=EVALUATOR_ID if outcome == "PASS" else None,
            evaluator_version=EVALUATOR_VERSION if outcome == "PASS" else None,
            test_results=tests,
            integration_ready=not blockers,
            integration_blockers=blockers,
        )

    def possible_duplicates(self, revision: ImplementationRevision, bindings: list[PolicyBinding],
                            tree: ScopeTree) -> list[dict[str, Any]]:
        """Flags by native id and document digest. Not a claim of semantic equivalence."""
        out = []
        for b in bindings:
            if b.provider != "azure":
                continue
            reasons = []
            if (b.definition_ref or "").lower() == (revision.source_ref or "").lower():
                reasons.append("same policy definition id")
            if b.definition_digest and b.definition_digest == revision.content_digest:
                reasons.append("same definition content digest")
            if reasons:
                out.append({
                    "binding_id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                    "settings": self._binding_settings(b), "management": b.management,
                    "reasons": reasons,
                    "note": "Possible overlap. Overlapping scopes must be compared with the rollout plan; "
                            "semantic equivalence is not claimed.",
                })
        return out

    # ---------------------------------------------------------------- assessment
    def _evaluate_config(self, resource_type: str, configuration: dict[str, Any], missing_fields: list[str]
                         ) -> ResourceEvaluation:
        if (resource_type or "").lower() != SEARCH_TYPE:
            return ResourceEvaluation(Applicability.NOT_APPLICABLE, ConfigurationResult.NOT_APPLICABLE,
                                      ["Policy rule targets Microsoft.Search/searchServices only."], {}, [])
        props = configuration.get("properties") if isinstance(configuration, dict) else None
        evidence = {"field": AZURE_SEARCH_PNA["alias"],
                    "rule": "publicNetworkAccess notEquals 'Disabled' (case-insensitive) => non-compliant"}
        if PNA_FIELD in missing_fields or not isinstance(props, dict) or "publicNetworkAccess" not in props:
            return ResourceEvaluation(
                Applicability.APPLICABLE, ConfigurationResult.UNKNOWN,
                ["publicNetworkAccess was not supplied by the inventory source. The evaluator does not infer a "
                 "provider default; Azure Policy itself would evaluate the live resource."],
                evidence, [PNA_FIELD])
        value = props["publicNetworkAccess"]
        evidence["observed_value"] = value
        if not isinstance(value, str):
            return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.UNKNOWN,
                                      [f"Unexpected publicNetworkAccess value type {type(value).__name__}."],
                                      evidence, [])
        if value.lower() == "disabled":
            return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.COMPLIANT,
                                      ["publicNetworkAccess is Disabled."], evidence, [])
        return ResourceEvaluation(
            Applicability.APPLICABLE, ConfigurationResult.NON_COMPLIANT,
            [f"publicNetworkAccess is {value!r}. Enforcing Deny does not change this existing configuration; "
             "remediation is a separate change by the owning team."], evidence, [])

    def assess_inventory(self, revision: ImplementationRevision, resources: list[ResourceSnapshot],
                         context: AssessmentContext) -> dict[str, ResourceEvaluation]:
        not_scopes = {s.lower() for s in (revision.assignment_settings or {}).get("notScopes", [])}
        out: dict[str, ResourceEvaluation] = {}
        for r in resources:
            ev = self._evaluate_config(r.resource_type, r.configuration, list(r.missing_fields or []))
            if ev.applicability != Applicability.NOT_APPLICABLE:
                scope_natives = {context.tree.scopes[s].native_id.lower()
                                 for s in context.tree.ancestors(r.scope_id)}
                if r.resource_id.lower() in not_scopes or scope_natives & not_scopes:
                    ev = ResourceEvaluation(Applicability.NOT_APPLICABLE, ConfigurationResult.NOT_APPLICABLE,
                                            ["Excluded by assignment notScopes."], {}, [])
            if ev.applicability == Applicability.APPLICABLE:
                ev.reasons.append("Configuration evidence only: this does not establish data exposure or "
                                  "network reachability.")
            out[r.resource_id] = ev
        return out

    def _binding_settings(self, b: PolicyBinding) -> dict[str, Any]:
        state = b.observed_state or b.desired_state or {}
        props = state.get("properties", {})
        effect = (props.get("parameters", {}).get("effect", {}) or {}).get("value") or b.settings.get("effect")
        mode = props.get("enforcementMode") or b.settings.get("enforcementMode", "Default")
        return {"effect": effect, "enforcementMode": mode,
                "source": "observed" if b.observed_state else "desired"}

    def baseline_for_scope(self, scope_id: str, resource_id: str | None, bindings: list[PolicyBinding],
                           tree: ScopeTree, now: datetime, observation_max_age: timedelta) -> dict[str, Any]:
        covering = []
        ancestors = set(tree.ancestors(scope_id))
        for b in bindings:
            if b.provider != "azure" or b.target_scope_id not in ancestors:
                continue
            if (b.definition_ref or "").lower() != AZURE_SEARCH_PNA["definition_id"].lower():
                continue
            excluded = {e.lower() for e in (b.exclusions or [])}
            natives = {tree.scopes[s].native_id.lower() for s in ancestors}
            if (resource_id and resource_id.lower() in excluded) or natives & excluded:
                continue
            s = self._binding_settings(b)
            fresh = b.observed_at is not None and now - b.observed_at <= observation_max_age
            covering.append({"binding_id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                             "effect": s["effect"], "enforcementMode": s["enforcementMode"],
                             "management": b.management, "observed_fresh": fresh,
                             "observed_at": iso(b.observed_at)})
        if any(c["effect"] == "Deny" and c["enforcementMode"] == "Default" and c["observed_fresh"]
               for c in covering):
            summary = "DENY_OBSERVED"
        elif any(c["effect"] == "Deny" and c["enforcementMode"] == "Default" for c in covering):
            summary = "DENY_UNVERIFIED"
        elif covering:
            summary = "AUDIT_ONLY"
        else:
            summary = "NONE"
        return {"summary": summary, "bindings": covering}

    def evaluate_supported_request_fixture(self, revision: ImplementationRevision, request: dict[str, Any],
                                           context: AssessmentContext,
                                           resource_lookup: dict[str, ResourceSnapshot],
                                           exempt_resource_ids: set[str]) -> RequestEvaluation:
        rtype = (request.get("resource_type") or "").lower()
        op = (request.get("operation") or "").upper()
        if rtype != SEARCH_TYPE:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Request resource type is not targeted by this policy rule."])
        if op == "DELETE":
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Deny evaluates create/update requests; delete is not denied by this rule."])
        if op not in {"PUT", "PATCH"}:
            return RequestEvaluation(RequestImpact.UNKNOWN, [f"Operation {op or '(missing)'} is not modelled."])
        scope_id = request.get("scope_id")
        if not scope_id or scope_id not in context.tree.scopes:
            return RequestEvaluation(RequestImpact.UNKNOWN, ["Request scope is missing or unknown."])
        if scope_id not in context.target_scope_ids:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Request is outside the evaluated assignment scope."])
        not_scopes = {s.lower() for s in (revision.assignment_settings or {}).get("notScopes", [])}
        natives = {context.tree.scopes[s].native_id.lower() for s in context.tree.ancestors(scope_id)}
        if natives & not_scopes or (request.get("resource_id") or "").lower() in not_scopes:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL, ["Excluded by notScopes."])
        effect = (revision.parameters or {}).get("effect")
        mode = (revision.assignment_settings or {}).get("enforcementMode", "Default")
        if effect != "Deny" or mode != "Default":
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     [f"Proposed assignment does not deny (effect {effect}, enforcementMode {mode})."])
        rid = request.get("resource_id")
        if rid and rid in exempt_resource_ids:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Resource is covered by an effective exemption for this assignment."])
        body = request.get("body") or {}
        props = body.get("properties") if isinstance(body, dict) else None
        if not isinstance(props, dict) or "publicNetworkAccess" not in props:
            why = ("PATCH without publicNetworkAccess: merge semantics are not modelled."
                   if op == "PATCH" else
                   "PUT without publicNetworkAccess: the evaluator does not infer the provider default.")
            return RequestEvaluation(RequestImpact.UNKNOWN, [why])
        value = props["publicNetworkAccess"]
        if isinstance(value, str) and value.lower() == "disabled":
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Requested publicNetworkAccess is Disabled. Not denied by this control; "
                                      "this is not proof the request is otherwise authorized."],
                                     {"requested_value": value})
        reasons = [f"Requested publicNetworkAccess {value!r} matches the Deny rule (403 RequestDisallowedByPolicy "
                   "expected from Resource Manager)."]
        return RequestEvaluation(RequestImpact.PREDICTED_DENIED, reasons, {"requested_value": value})

    def readiness_for_resource(self, resource: ResourceSnapshot, evaluation: ResourceEvaluation,
                               evidence: dict[str, EvidenceView], exception_state: dict[str, Any],
                               context: AssessmentContext) -> tuple[str | None, list[str], list[Blocker]]:
        if evaluation.applicability == Applicability.NOT_APPLICABLE:
            return None, ["Not applicable."], []
        blockers: list[Blocker] = []
        reasons: list[str] = []
        rid, scope, app = resource.resource_id, resource.scope_id, resource.application
        owner_team = app or "Unassigned (ownership missing)"
        if not app:
            blockers.append(Blocker("MISSING_APPLICATION", rid, scope, None,
                                    "No application/business owner recorded for this resource.",
                                    "Identify the owning application team (ownership lookup).",
                                    "Cloud Security"))
        if not resource.owner:
            blockers.append(Blocker("MISSING_OWNER", rid, scope, app, "No technical owner recorded.",
                                    "Record an owner in the inventory source.", "Cloud Security"))
        cfg = evaluation.configuration_result
        disposition = exception_state.get("disposition", ExceptionDisposition.NONE)
        if cfg == ConfigurationResult.UNKNOWN:
            blockers.append(Blocker("CONFIGURATION_UNKNOWN", rid, scope, app,
                                    "publicNetworkAccess is unknown in the snapshot.",
                                    "Collect the property from an authoritative inventory source and reassess.",
                                    "Cloud Engineering"))
        if cfg == ConfigurationResult.NON_COMPLIANT:
            if disposition == ExceptionDisposition.EFFECTIVE and exception_state.get("covers_proposed"):
                reasons.append("Covered by an effective exemption for the proposed assignment.")
            elif disposition in (ExceptionDisposition.EFFECTIVE, ExceptionDisposition.APPROVED_UNAPPLIED):
                blockers.append(Blocker(
                    "EXCEPTION_NOT_APPLIED", rid, scope, app,
                    "An approved exception exists but no exemption references the proposed assignment yet "
                    "(exemptions are per assignment).",
                    "Include the exemption in the change package so it is applied before enforcement.",
                    "Cloud Engineering"))
            elif disposition == ExceptionDisposition.PENDING:
                blockers.append(Blocker("EXCEPTION_PENDING", rid, scope, app,
                                        "An exception request is still pending a decision.",
                                        "Security approver decides the request, or the team remediates.",
                                        "Cloud Security"))
            else:
                extra = " (previous exception expired)" if disposition == ExceptionDisposition.EXPIRED else ""
                blockers.append(Blocker(
                    "EXISTING_VIOLATION", rid, scope, app,
                    f"publicNetworkAccess is enabled{extra}. Deny would reject future create/update requests that "
                    "keep it enabled; it does not remediate the existing configuration.",
                    "Owning team remediates (private endpoint + disable public access) via a linked work item, "
                    "or requests a time-bound exception.", owner_team))
        exempt_ok = cfg == ConfigurationResult.NON_COMPLIANT and disposition == ExceptionDisposition.EFFECTIVE \
            and exception_state.get("covers_proposed")
        # Resources on an approved exception path are exempted rather than migrated, so private-path readiness
        # evidence is not required for them; the exemption itself is the gating item.
        exception_path = cfg == ConfigurationResult.NON_COMPLIANT and disposition in (
            ExceptionDisposition.EFFECTIVE, ExceptionDisposition.APPROVED_UNAPPLIED)
        if exception_path and not exempt_ok:
            reasons.append("On an approved exception path: readiness depends on the exemption being applied.")
        if cfg in (ConfigurationResult.COMPLIANT, ConfigurationResult.NON_COMPLIANT) and not exception_path:
            for p in PREREQUISITES:
                ev = evidence.get(p["id"])
                if ev is None:
                    blockers.append(Blocker("EVIDENCE_MISSING", f"{rid}#{p['id']}", scope, app,
                                            f"No {p['label'].lower()} evidence supplied.",
                                            f"Supply {p['label'].lower()} evidence.", p["owner"]))
                elif ev.stale:
                    blockers.append(Blocker("EVIDENCE_STALE", f"{rid}#{p['id']}", scope, app,
                                            f"{p['label']} evidence from {iso(ev.collected_at)} is stale.",
                                            "Refresh the evidence.", p["owner"]))
                elif ev.status == "NOT_SATISFIED":
                    blockers.append(Blocker("EVIDENCE_NOT_SATISFIED", f"{rid}#{p['id']}", scope, app,
                                            f"{p['label']} is not satisfied: {ev.summary}",
                                            "Complete the prerequisite and supply new evidence.", p["owner"]))
                elif ev.status == "UNKNOWN":
                    blockers.append(Blocker("EVIDENCE_UNKNOWN", f"{rid}#{p['id']}", scope, app,
                                            f"{p['label']} status is unknown.",
                                            "Supply conclusive evidence.", p["owner"]))
        if any(b.kind in {"EVIDENCE_NOT_SATISFIED", "EXISTING_VIOLATION"} for b in blockers):
            readiness = Readiness.BLOCKED
        elif blockers:
            readiness = Readiness.UNKNOWN if all(
                b.kind in {"EVIDENCE_MISSING", "EVIDENCE_STALE", "EVIDENCE_UNKNOWN", "CONFIGURATION_UNKNOWN",
                           "MISSING_OWNER", "MISSING_APPLICATION"} for b in blockers) else Readiness.BLOCKED
        else:
            readiness = Readiness.READY
            reasons.append("All readiness prerequisites have fresh, satisfied evidence." if not exempt_ok
                           else "Exempt for the proposed assignment.")
        return readiness, reasons, blockers

    # ---------------------------------------------------------------- exceptions
    def exception_covers_resource(self, exception: SecurityException, resource: ResourceSnapshot,
                                  tree: ScopeTree) -> bool:
        if exception.granularity not in (ExceptionGranularity.SCOPE, ExceptionGranularity.RESOURCE):
            return False
        if not tree.is_within(resource.scope_id, exception.scope_id):
            return False
        if exception.resource_ids:
            return resource.resource_id in exception.resource_ids
        return True

    def validate_exception_representation(self, exception: SecurityException, revision: ImplementationRevision,
                                          bindings: list[PolicyBinding], tree: ScopeTree
                                          ) -> ExceptionRepresentation:
        scope = tree.scopes.get(exception.scope_id)
        if exception.granularity == ExceptionGranularity.PRINCIPAL:
            return ExceptionRepresentation(
                Representability.UNSUPPORTED,
                "Principal-based exemptions (resourceSelectors userPrincipalId/groupPrincipalId) are documented by "
                "Azure but are not modelled or verified for this control in the MVP.", None, True)
        if scope is None or scope.provider != "azure" or scope.scope_type not in (
                ScopeType.AZURE_MANAGEMENT_GROUP, ScopeType.AZURE_SUBSCRIPTION, ScopeType.AZURE_RESOURCE_GROUP):
            return ExceptionRepresentation(Representability.UNSUPPORTED,
                                           "Exemption scope must be an Azure management group, subscription, "
                                           "resource group or resources beneath one.", None, True)
        if exception.granularity == ExceptionGranularity.RESOURCE and not exception.resource_ids:
            return ExceptionRepresentation(Representability.UNSUPPORTED,
                                           "Resource-level exception must list resource ids.", None, True)
        targets = list(exception.resource_ids) if exception.granularity == ExceptionGranularity.RESOURCE \
            else [scope.native_id]
        representation = {
            "type": "Microsoft.Authorization/policyExemptions",
            "targets": targets,
            "policyAssignmentId": "resolved when the change package selects the covering assignment",
            "policyDefinitionReferenceId": None,
            "exemptionCategory": "Waiver",
            "expiresOn": format_expires_on(exception.expires_at),
            "assignmentScopeValidation": "Default",
            "note": "Exemptions reference exactly one assignment. Each new assignment requires its own exemption. "
                    "After expiresOn Azure stops honoring the exemption; the object remains until deleted.",
        }
        return ExceptionRepresentation(Representability.REPRESENTABLE,
                                       "Representable as a policy exemption with native expiry (expiresOn).",
                                       representation, True)

    # ---------------------------------------------------------------- rings and artifacts
    def validate_ring_settings(self, stage: str, settings: dict[str, Any],
                               revision: ImplementationRevision) -> list[Check]:
        checks: list[Check] = []
        allowed_keys = {"effect", "enforcementMode", "use_existing_binding_id"}
        unknown = set(settings) - allowed_keys
        if unknown:
            checks.append(Check(f"ring.{stage}", "FAIL", f"Unknown ring settings {sorted(unknown)}."))
        if settings.get("use_existing_binding_id"):
            if stage != RolloutStage.OBSERVATION:
                checks.append(Check(f"ring.{stage}", "FAIL",
                                    "Existing bindings may only be reused for observation."))
            return checks or [Check(f"ring.{stage}", "PASS", "Reuses existing observation binding.")]
        effect = settings.get("effect")
        mode = settings.get("enforcementMode", "Default")
        if effect not in AZURE_SEARCH_PNA["allowed_effects"]:
            checks.append(Check(f"ring.{stage}.effect", "UNSUPPORTED",
                                f"Effect {effect!r} not in definition allowedValues."))
        elif effect == "Disabled":
            checks.append(Check(f"ring.{stage}.effect", "FAIL", "Disabled does not implement the control."))
        if mode not in ENFORCEMENT_MODES:
            checks.append(Check(f"ring.{stage}.enforcementMode", "UNSUPPORTED", f"enforcementMode {mode!r}."))
        enforcing = effect == "Deny" and mode == "Default"
        if stage == RolloutStage.OBSERVATION and enforcing:
            checks.append(Check(f"ring.{stage}", "FAIL", "Observation ring must not enforce (use Audit or "
                                                          "DoNotEnforce)."))
        if stage in (RolloutStage.PILOT, RolloutStage.LIMITED, RolloutStage.BROAD) and not enforcing:
            checks.append(Check(f"ring.{stage}", "WARN", "Ring does not enforce; it only observes."))
        if not checks or all(c.status in ("PASS", "WARN") for c in checks):
            checks.append(Check(f"ring.{stage}", "PASS", f"effect {effect}, enforcementMode {mode}."))
        return checks

    def ring_is_enforcing(self, settings: dict[str, Any]) -> bool:
        return (not settings.get("use_existing_binding_id") and settings.get("effect") == "Deny"
                and settings.get("enforcementMode", "Default") == "Default")

    def ring_needs_artifact(self, settings: dict[str, Any]) -> bool:
        return not settings.get("use_existing_binding_id")

    def assignment_name(self, control_id: str, implementation_id: str, scope_id: str) -> str:
        # Management-group assignment names are length-limited; keep names short and deterministic.
        return "ccp-" + short_hash(control_id, implementation_id, scope_id, length=16)

    def assignment_identity(self, scope_native_id: str, name: str) -> str:
        return f"{scope_native_id}/providers/Microsoft.Authorization/policyAssignments/{name}"

    def generate_change_artifacts(self, package: dict[str, Any], revision: ImplementationRevision,
                                  tree: ScopeTree) -> list[Artifact]:
        artifacts: list[Artifact] = []
        control = package["control"]
        assignment_by_scope: dict[str, str] = {}
        impl_id = revision.implementation_id
        for ring in package["deploy_rings"]:
            settings = ring["settings"]
            if not self.ring_needs_artifact(settings):
                continue
            for scope_id in ring["scope_ids"]:
                scope = tree.get(scope_id)
                name = self.assignment_name(control["id"], impl_id, scope_id)
                identity = self.assignment_identity(scope.native_id, name)
                assignment_by_scope[scope_id] = identity
                not_scopes = sorted((revision.assignment_settings or {}).get("notScopes", []))
                content = {
                    "name": name,
                    "scope": scope.native_id,
                    "properties": {
                        "displayName": f"{control['name']} ({ring['stage'].lower()})",
                        "description": f"Implements control {control['id']} revision {control['revision']}. "
                                       "References the built-in definition; no custom copy is deployed.",
                        "policyDefinitionId": revision.source_ref,
                        "parameters": {"effect": {"value": settings["effect"]}},
                        "enforcementMode": settings.get("enforcementMode", "Default"),
                        "notScopes": not_scopes,
                        "nonComplianceMessages": [{"message": (revision.assignment_settings or {}).get(
                            "nonComplianceMessage",
                            "Public network access must be disabled for Azure AI Search. "
                            "Request an exception through Cloud Security if required.")}],
                        "metadata": {
                            "ccpControlId": control["id"],
                            "ccpControlRevision": control["revision"],
                            "ccpImplementationRevisionId": revision.id,
                            "ccpPinnedDefinitionVersion": revision.pinned_version,
                            "ccpDefinitionDigest": revision.content_digest,
                        },
                    },
                }
                artifacts.append(Artifact(
                    path=f"native/azure/policy-assignment-{scope_id}.json",
                    kind="AZURE_POLICY_ASSIGNMENT", ring_stage=ring["stage"], scope_id=scope_id,
                    native_identity=identity, content=content, enforcing=self.ring_is_enforcing(settings),
                    notes=["The assignment references the built-in by id; Microsoft may publish newer definition "
                           "versions. The pinned version/digest is recorded in metadata for drift detection."]))
        for addition in package["exception_set"]["additions"]:
            covering_scope = None
            for ring in package["deploy_rings"]:
                for sid in ring["scope_ids"]:
                    if sid in assignment_by_scope and tree.is_within(addition["scope_id"], sid):
                        covering_scope = sid
            if covering_scope is None:
                continue
            ring_stage = next(r["stage"] for r in package["deploy_rings"] if covering_scope in r["scope_ids"])
            targets = addition["resource_ids"] or [tree.get(addition["scope_id"]).native_id]
            for target in targets:
                name = "ccp-exm-" + short_hash(addition["exception_id"], target, assignment_by_scope[covering_scope])
                identity = f"{target}/providers/Microsoft.Authorization/policyExemptions/{name}"
                content = {
                    "name": name,
                    "scope": target,
                    "properties": {
                        "displayName": f"Exception {addition['exception_id']} ({addition['application']})",
                        "description": addition["business_justification"][:500],
                        "policyAssignmentId": assignment_by_scope[covering_scope],
                        "exemptionCategory": "Waiver",
                        "expiresOn": format_expires_on(parse_utc(addition["expires_at"])),
                        "assignmentScopeValidation": "Default",
                        "metadata": {
                            "requestedBy": addition["requester"],
                            "approvedBy": addition["approved_by"],
                            "ccpExceptionId": addition["exception_id"],
                            "riskOwner": addition["risk_owner"],
                        },
                    },
                }
                artifacts.append(Artifact(
                    path=f"native/azure/policy-exemption-{addition['exception_id']}-{short_hash(target)}.json",
                    kind="AZURE_POLICY_EXEMPTION", ring_stage=ring_stage, scope_id=addition["scope_id"],
                    native_identity=identity, content=content, enforcing=False,
                    exception_id=addition["exception_id"],
                    notes=["Apply before the enforcing assignment in the same ring."]))
        return artifacts

    # ---------------------------------------------------------------- observation
    def compare_observed_state(self, expected: dict[str, Any], observed: dict[str, Any] | None,
                               artifact_kind: str) -> Comparison:
        if observed is None:
            return Comparison("MISSING", ["Native object not observed."])
        diffs: list[str] = []
        ep, op = expected.get("properties", {}), observed.get("properties", {})
        if _lower(expected.get("scope")) != _lower(observed.get("scope")):
            diffs.append(f"scope: expected {expected.get('scope')}, observed {observed.get('scope')}")
        if artifact_kind == "AZURE_POLICY_ASSIGNMENT":
            pairs = [
                ("policyDefinitionId", _lower(ep.get("policyDefinitionId")), _lower(op.get("policyDefinitionId"))),
                ("parameters.effect", _lower((ep.get("parameters", {}).get("effect") or {}).get("value")),
                 _lower((op.get("parameters", {}).get("effect") or {}).get("value"))),
                ("enforcementMode", ep.get("enforcementMode", "Default"), op.get("enforcementMode", "Default")),
                ("notScopes", sorted(_lower(s) for s in ep.get("notScopes", [])),
                 sorted(_lower(s) for s in op.get("notScopes", []))),
            ]
        else:
            def _exp(v):
                try:
                    return parse_utc(v) if v else None
                except ValueError:
                    return v
            pairs = [
                ("policyAssignmentId", _lower(ep.get("policyAssignmentId")), _lower(op.get("policyAssignmentId"))),
                ("exemptionCategory", _lower(ep.get("exemptionCategory")), _lower(op.get("exemptionCategory"))),
                ("expiresOn", _exp(ep.get("expiresOn")), _exp(op.get("expiresOn"))),
            ]
        for name, e, o in pairs:
            if e != o:
                diffs.append(f"{name}: expected {e}, observed {o}")
        return Comparison("MATCH" if not diffs else "MISMATCH", diffs)

    def mock_observed_state(self, expected: dict[str, Any], scenario: str, artifact_kind: str
                            ) -> dict[str, Any] | None:
        if scenario == "MISSING":
            return None
        observed = copy.deepcopy(expected)
        if scenario == "DRIFT":
            if artifact_kind == "AZURE_POLICY_ASSIGNMENT":
                observed["properties"]["enforcementMode"] = "DoNotEnforce"
            else:
                observed["properties"]["expiresOn"] = None
        return observed
