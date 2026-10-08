"""AWS Service Control Policy provider.

SCPs constrain permissions; they do not configure resources. The supported template protects
an already-established S3 Block Public Access baseline by denying changes to it (except for
named administrative principals). It cannot inspect requested values, so it also blocks
legitimate strengthening changes by non-exempt principals.

SCPs have no audit effect: observation is offline assessment only, and this provider refuses
any "Audit" setting. Documentation was not verified from a primary source in this build, so
every implementation here is DEMO-ONLY (integration-ready blocked).
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import iso
from app.core.digests import digest
from app.models.entities import ImplementationRevision, PolicyBinding, ResourceSnapshot, SecurityException
from app.models.enums import (
    Applicability,
    ConfigurationResult,
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
from app.providers.common import Blocker, EvidenceView, arn_like, short_hash
from app.providers.verification import AWS_VERIFICATION
from app.services.scopes import ScopeTree

POLICY_KIND = "AWS_SCP"
TEMPLATE_REF = "template:aws.scp.protect-s3-block-public-access"
TEMPLATE_VERSION = "1.0.0"
EVALUATOR_ID = "aws.scp.protect-s3-block-public-access"
EVALUATOR_VERSION = "1.0.0"

ACCOUNT_ACTION = "s3:PutAccountPublicAccessBlock"
BUCKET_ACTION = "s3:PutBucketPublicAccessBlock"
PROTECTED_ACTIONS = [ACCOUNT_ACTION, BUCKET_ACTION]
BPA_FLAGS = ["BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"]
ACCOUNT_BPA_TYPE = "AWS::S3::AccountPublicAccessBlock"
BUCKET_TYPE = "AWS::S3::Bucket"
PRINCIPAL_PATTERN = re.compile(r"^arn:aws:iam::(\*|\d{12}):role/[A-Za-z0-9+=,.@_/*-]{1,200}$")

PREREQUISITES = [
    {"id": "S3_ACCOUNT_BPA_BASELINE", "label": "Account-level Block Public Access baseline",
     "owner": "Cloud Engineering",
     "description": "All four account-level settings enabled, with fresh provenance. Derived from inventory."},
    {"id": "BPA_ADMIN_PROCESS", "label": "Block Public Access administration path", "owner": "Cloud Engineering",
     "description": "Exempt administrative role exists and the change/rollback procedure is documented, because "
                    "the SCP blocks all other principals from changing these settings."},
]


def render_template(parameters: dict[str, Any]) -> dict[str, Any]:
    patterns = sorted(parameters.get("exempt_principal_arn_patterns", []))
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ProtectS3BlockPublicAccessBaseline",
                "Effect": "Deny",
                "Action": list(PROTECTED_ACTIONS),
                "Resource": "*",
                "Condition": {"ArnNotLike": {"aws:PrincipalArn": patterns}},
            }
        ],
    }


def parse_protective_document(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Recognise only the narrow template family (Deny on BPA actions, optional ArnNotLike exemption).

    Returns {"actions": [...], "exempt_patterns": [...]} or None if the document is outside the family.
    Arbitrary SCPs are never interpreted.
    """
    if not isinstance(doc, dict) or doc.get("Version") != "2012-10-17":
        return None
    stmts = doc.get("Statement")
    if not isinstance(stmts, list) or len(stmts) != 1:
        return None
    s = stmts[0]
    if set(s) - {"Sid", "Effect", "Action", "Resource", "Condition"}:
        return None
    if s.get("Effect") != "Deny" or s.get("Resource") != "*":
        return None
    actions = s.get("Action")
    actions = [actions] if isinstance(actions, str) else actions
    if not isinstance(actions, list) or not actions or not set(actions) <= set(PROTECTED_ACTIONS):
        return None
    cond = s.get("Condition")
    patterns: list[str] = []
    if cond is not None:
        if set(cond) != {"ArnNotLike"} or set(cond["ArnNotLike"]) != {"aws:PrincipalArn"}:
            return None
        p = cond["ArnNotLike"]["aws:PrincipalArn"]
        patterns = [p] if isinstance(p, str) else list(p)
    return {"actions": sorted(actions), "exempt_patterns": patterns}


def _flags(config: dict[str, Any] | None) -> dict[str, bool | None]:
    if not isinstance(config, dict):
        return {f: None for f in BPA_FLAGS}
    return {f: config.get(f) if isinstance(config.get(f), bool) else None for f in BPA_FLAGS}


class AwsScpProvider:
    name = "aws"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider="aws",
            policy_kinds=[POLICY_KIND],
            supported_scope_types=[ScopeType.AWS_ROOT, ScopeType.AWS_OU, ScopeType.AWS_ACCOUNT],
            supported_effects=["Deny statement"],
            native_audit_mode="None. SCPs have no audit effect; nothing is ever deployed 'in audit mode'.",
            observation_method="Offline assessment of supplied inventory and request fixtures only. Later: analysis "
            "of relevant evidence (deferred).",
            validation_methods=[
                "Document must equal the supported template rendered from its parameters (digest match)",
                "Exempt principal patterns validated as IAM role ARN patterns",
                "Deterministic evaluator test vectors",
                "Overlap detection against existing SCP bindings by action set and scope ancestry",
            ],
            assessment_limitations=[
                "SCPs do not affect the management account or service-linked roles.",
                "SCPs restrict permissions; they do not establish or repair Block Public Access settings.",
                "The SCP cannot inspect requested Block Public Access values; it blocks strengthening changes too.",
                "Arbitrary SCP documents are not interpreted; only the supported template family is evaluated.",
                "Request results are not proof of overall authorization (identity, resource, permission boundary "
                "and session policies are not evaluated).",
                "Directory buckets are excluded from evaluation.",
            ],
            exemption_mechanism="No native exemption object. Possible representations: exempt principal ARN "
            "pattern in the SCP condition (new implementation revision), or omitting an account from "
            "account-level attachments. No native expiry.",
            exemption_granularities={
                ExceptionGranularity.PRINCIPAL: "Representable as an ArnNotLike pattern; applies wherever the SCP "
                "is attached. Requires a reviewed implementation revision.",
                ExceptionGranularity.SCOPE: "Account-level only, and only when attachments are per account.",
                ExceptionGranularity.RESOURCE: "Not representable: an SCP cannot exempt one bucket and a "
                "bucket-level exception cannot override account/organization Block Public Access.",
            },
            native_expiry_supported=False,
            artifact_kinds=["AWS_SCP_DOCUMENT", "AWS_SCP_ATTACHMENT"],
            ring_settings={
                RolloutStage.OBSERVATION: "mode OFFLINE_ASSESSMENT only (no artifact).",
                RolloutStage.PILOT: "attach true at account scopes with a verified baseline.",
                RolloutStage.LIMITED: "attach true.",
                RolloutStage.BROAD: "attach true.",
            },
            integration_status=AWS_VERIFICATION["status"],
        )

    def readiness_prerequisites(self) -> list[dict[str, str]]:
        return PREREQUISITES

    # ---------------------------------------------------------------- validation
    def _support_checks(self, revision: ImplementationRevision) -> list[Check]:
        checks: list[Check] = []
        if revision.policy_kind != POLICY_KIND:
            return [Check("policy_kind", "UNSUPPORTED", f"Policy kind {revision.policy_kind} has no AWS evaluator.")]
        checks.append(Check("policy_kind", "PASS", "Service control policy."))
        params = revision.parameters or {}
        for forbidden in ("effect", "mode", "audit"):
            if forbidden in {k.lower() for k in params}:
                checks.append(Check("parameters.effect", "UNSUPPORTED",
                                    "SCPs have no audit/effect switch. Observation for SCPs is offline assessment; "
                                    "an 'Audit SCP' cannot be emitted or deployed."))
        if revision.source_ref != TEMPLATE_REF or revision.pinned_version != TEMPLATE_VERSION:
            checks.append(Check("template", "UNSUPPORTED",
                                f"Only {TEMPLATE_REF}@{TEMPLATE_VERSION} is supported."))
        if set(params) - {"exempt_principal_arn_patterns"} and not any(c.status == "UNSUPPORTED" for c in checks):
            checks.append(Check("parameters", "FAIL", f"Unknown parameters {sorted(set(params))}."))
        patterns = params.get("exempt_principal_arn_patterns")
        if not isinstance(patterns, list) or not 1 <= len(patterns) <= 5:
            checks.append(Check("parameters.exempt_principal_arn_patterns", "FAIL",
                                "Provide 1-5 exempt administrative role ARN patterns (legitimate administration "
                                "must remain possible)."))
        elif not all(isinstance(p, str) and PRINCIPAL_PATTERN.match(p) for p in patterns):
            checks.append(Check("parameters.exempt_principal_arn_patterns", "FAIL",
                                "Patterns must look like arn:aws:iam::<account|*>:role/<name>."))
        else:
            checks.append(Check("parameters.exempt_principal_arn_patterns", "PASS",
                                f"{len(patterns)} exempt administrative principal pattern(s)."))
        doc = revision.native_document
        if any(c.status in ("FAIL", "UNSUPPORTED") for c in checks):
            return checks
        if doc is None or digest(doc) != digest(render_template(params)):
            checks.append(Check("document_template", "UNSUPPORTED",
                                "SCP document does not equal the supported template rendering; arbitrary SCP "
                                "syntax is not interpreted and no evaluator is reused."))
        else:
            checks.append(Check("document_template", "PASS", "Document equals the template rendering."))
            if revision.content_digest != digest(doc):
                checks.append(Check("content_digest_integrity", "FAIL", "Recorded digest does not match."))
        return checks

    def evaluator_for(self, revision: ImplementationRevision) -> tuple[str, str] | None:
        if outcome_from_checks(self._support_checks(revision)) == "PASS":
            return EVALUATOR_ID, EVALUATOR_VERSION
        return None

    def _test_vectors(self, revision: ImplementationRevision) -> list[dict[str, Any]]:
        results = []
        all_true = {f: True for f in BPA_FLAGS}
        one_false = dict(all_true, BlockPublicPolicy=False)
        cases = [
            ("account-all-enabled", self._eval_account({"PublicAccessBlockConfiguration": all_true}, []),
             ConfigurationResult.COMPLIANT),
            ("account-one-disabled", self._eval_account({"PublicAccessBlockConfiguration": one_false}, []),
             ConfigurationResult.NON_COMPLIANT),
            ("account-missing", self._eval_account({}, ["PublicAccessBlockConfiguration"]),
             ConfigurationResult.UNKNOWN),
        ]
        for name, ev, expected in cases:
            results.append({"name": name, "expected": expected.value, "actual": ev.configuration_result,
                            "passed": ev.configuration_result == expected.value})
        patterns = revision.parameters.get("exempt_principal_arn_patterns", [])
        if patterns:
            sample = patterns[0].replace("*", "123456789012")
            matched = any(arn_like(sample, p) for p in patterns)
            results.append({"name": "exempt-principal-not-denied", "expected": True, "actual": matched,
                            "passed": matched})
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
        checks.append(Check("audit_mode", "PASS", "No audit artifact will be generated (SCPs have no audit effect)."))
        blockers = []
        if outcome != "PASS":
            blockers.append("Validation did not pass.")
        if revision.verification.get("status") != VerificationStatus.VERIFIED_PRIMARY_SOURCE:
            blockers.append("AWS documentation was not verified from a primary source in this build: DEMO-ONLY.")
        return ValidationReport(outcome, checks, EVALUATOR_ID if outcome == "PASS" else None,
                                EVALUATOR_VERSION if outcome == "PASS" else None, tests, not blockers, blockers)

    def possible_duplicates(self, revision: ImplementationRevision, bindings: list[PolicyBinding],
                            tree: ScopeTree) -> list[dict[str, Any]]:
        mine = parse_protective_document(revision.native_document)
        out = []
        for b in bindings:
            if b.provider != "aws":
                continue
            reasons = []
            if b.definition_digest and b.definition_digest == revision.content_digest:
                reasons.append("same SCP document digest")
            theirs = parse_protective_document((b.observed_state or b.desired_state or {}).get("Content"))
            if mine and theirs and set(mine["actions"]) & set(theirs["actions"]):
                reasons.append(f"overlapping denied actions {sorted(set(mine['actions']) & set(theirs['actions']))}")
            if reasons:
                out.append({"binding_id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                            "management": b.management, "reasons": reasons,
                            "note": "Possible overlap; inherited denies at this scope apply to all descendants. "
                                    "Semantic equivalence is not claimed."})
        return out

    # ---------------------------------------------------------------- assessment
    def _eval_account(self, configuration: dict[str, Any], missing: list[str]) -> ResourceEvaluation:
        if "PublicAccessBlockConfiguration" in missing or "PublicAccessBlockConfiguration" not in configuration:
            return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.UNKNOWN,
                                      ["Account-level Block Public Access configuration was not supplied."],
                                      {}, ["PublicAccessBlockConfiguration"])
        flags = _flags(configuration.get("PublicAccessBlockConfiguration"))
        evidence = {"account_level": flags}
        if any(v is None for v in flags.values()):
            return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.UNKNOWN,
                                      ["One or more account-level settings are missing."], evidence,
                                      [f"PublicAccessBlockConfiguration.{k}" for k, v in flags.items() if v is None])
        if all(flags.values()):
            return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.COMPLIANT,
                                      ["All four account-level Block Public Access settings are enabled."], evidence, [])
        off = [k for k, v in flags.items() if not v]
        return ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.NON_COMPLIANT,
                                  [f"Account-level settings disabled: {off}. Protective baseline is not established; "
                                   "an SCP would protect an insecure configuration."], evidence, [])

    def assess_inventory(self, revision: ImplementationRevision, resources: list[ResourceSnapshot],
                         context: AssessmentContext) -> dict[str, ResourceEvaluation]:
        out: dict[str, ResourceEvaluation] = {}
        account_flags: dict[str, dict[str, bool | None]] = {}
        for r in resources:
            if r.resource_type == ACCOUNT_BPA_TYPE:
                ev = self._eval_account(r.configuration, list(r.missing_fields or []))
                account_flags[r.scope_id] = ev.evidence.get("account_level", {f: None for f in BPA_FLAGS})
                self._annotate_enforcement(ev, r.scope_id, context.tree)
                out[r.resource_id] = ev
        context_accounts = account_flags
        for r in resources:
            if r.resource_type == ACCOUNT_BPA_TYPE:
                continue
            if r.resource_type != BUCKET_TYPE:
                out[r.resource_id] = ResourceEvaluation(Applicability.NOT_APPLICABLE,
                                                        ConfigurationResult.NOT_APPLICABLE,
                                                        ["Resource type not evaluated by this control."], {}, [])
                continue
            bucket_type = (r.configuration or {}).get("bucketType")
            if "bucketType" in (r.missing_fields or []) or bucket_type is None:
                out[r.resource_id] = ResourceEvaluation(
                    Applicability.UNKNOWN, ConfigurationResult.UNKNOWN,
                    ["Bucket type not supplied; directory buckets are excluded, so applicability is unknown."],
                    {}, ["bucketType"])
                continue
            if bucket_type != "general-purpose":
                out[r.resource_id] = ResourceEvaluation(
                    Applicability.NOT_APPLICABLE, ConfigurationResult.NOT_APPLICABLE,
                    [f"Bucket type {bucket_type!r} excluded: only general purpose buckets are evaluated."], {}, [])
                continue
            acct = context_accounts.get(r.scope_id, {f: None for f in BPA_FLAGS})
            cfg = r.configuration or {}
            if cfg.get("PublicAccessBlockConfigurationStatus") == "NOT_CONFIGURED":
                # The collector explicitly observed that no bucket-level configuration exists. This is an
                # observation in the evidence, not an inferred default.
                bucket = {f: False for f in BPA_FLAGS}
            elif "PublicAccessBlockConfiguration" in (r.missing_fields or []):
                bucket = {f: None for f in BPA_FLAGS}
            else:
                bucket = _flags(cfg.get("PublicAccessBlockConfiguration"))
            effective: dict[str, bool | None] = {}
            for f in BPA_FLAGS:
                a, b = acct.get(f), bucket.get(f)
                effective[f] = True if (a is True or b is True) else (None if (a is None or b is None) else False)
            evidence = {"account_level": acct, "bucket_level": bucket, "effective": effective}
            if all(v is True for v in effective.values()):
                ev = ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.COMPLIANT,
                                        ["Effective Block Public Access (account OR bucket level) is fully enabled."],
                                        evidence, [])
            elif any(v is False for v in effective.values()):
                off = [k for k, v in effective.items() if v is False]
                ev = ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.NON_COMPLIANT,
                                        [f"Effective settings disabled: {off}. This is configuration evidence, not "
                                         "proof of unauthenticated data exposure."], evidence, [])
            else:
                ev = ResourceEvaluation(Applicability.APPLICABLE, ConfigurationResult.UNKNOWN,
                                        ["Effective settings cannot be determined from supplied data."], evidence,
                                        ["PublicAccessBlockConfiguration"])
            self._annotate_enforcement(ev, r.scope_id, context.tree)
            out[r.resource_id] = ev
        context.extra["account_baselines"] = {
            sid: ("COMPLIANT" if all(v is True for v in f.values())
                  else "NON_COMPLIANT" if any(v is False for v in f.values()) else "UNKNOWN")
            for sid, f in account_flags.items()
        }
        return out

    def _annotate_enforcement(self, ev: ResourceEvaluation, scope_id: str, tree: ScopeTree) -> None:
        scope = tree.scopes.get(scope_id)
        if scope is not None and scope.is_management_account:
            ev.enforcement_applies = False
            ev.reasons.append("Management account: SCPs do not restrict its principals. This baseline needs a "
                              "different mechanism (e.g. Organizations S3 policy, to be evaluated with Cloud "
                              "Engineering).")

    def _inherited_denies(self, account_scope_id: str, action: str, principal_arn: str | None,
                          bindings: list[PolicyBinding], tree: ScopeTree) -> list[dict[str, Any]]:
        hits = []
        ancestors = set(tree.ancestors(account_scope_id))
        for b in bindings:
            if b.provider != "aws" or b.target_scope_id not in ancestors:
                continue
            parsed = parse_protective_document((b.observed_state or b.desired_state or {}).get("Content"))
            if parsed is None or action not in parsed["actions"]:
                continue
            exempt = principal_arn is not None and any(arn_like(principal_arn, p) for p in parsed["exempt_patterns"])
            hits.append({"binding_id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                         "principal_exempt": exempt, "management": b.management})
        return hits

    def baseline_for_scope(self, scope_id: str, resource_id: str | None, bindings: list[PolicyBinding],
                           tree: ScopeTree, now: datetime, observation_max_age: timedelta) -> dict[str, Any]:
        covering = []
        ancestors = set(tree.ancestors(scope_id))
        for b in bindings:
            if b.provider != "aws" or b.target_scope_id not in ancestors:
                continue
            parsed = parse_protective_document((b.observed_state or b.desired_state or {}).get("Content"))
            fresh = b.observed_at is not None and now - b.observed_at <= observation_max_age
            covering.append({"binding_id": b.id, "native_id": b.native_id, "scope_id": b.target_scope_id,
                             "denied_actions": parsed["actions"] if parsed else None,
                             "evaluable": parsed is not None, "management": b.management,
                             "observed_fresh": fresh, "observed_at": iso(b.observed_at),
                             "inherited": b.target_scope_id != scope_id})
        actions = {a for c in covering if c["denied_actions"] for a in c["denied_actions"]}
        if set(PROTECTED_ACTIONS) <= actions:
            summary = "FULL_ACTION_COVERAGE"
        elif actions:
            summary = "PARTIAL_ACTION_COVERAGE"
        else:
            summary = "NONE"
        return {"summary": summary, "bindings": covering}

    def evaluate_supported_request_fixture(self, revision: ImplementationRevision, request: dict[str, Any],
                                           context: AssessmentContext,
                                           resource_lookup: dict[str, ResourceSnapshot],
                                           exempt_resource_ids: set[str]) -> RequestEvaluation:
        action = request.get("action")
        account = request.get("account_scope_id")
        principal = request.get("principal_arn")
        ptype = (request.get("principal_type") or "").upper()
        baseline: dict[str, Any] = {}
        if account in context.tree.scopes and principal:
            inherited = self._inherited_denies(account, action or "", principal, context.bindings, context.tree)
            denying = [h for h in inherited if not h["principal_exempt"]]
            baseline = {"inherited_denies": inherited,
                        "already_denied_by_existing": bool(denying) and not context.tree.scopes[account]
                        .is_management_account and ptype != "SERVICE_LINKED_ROLE"}
        if action not in PROTECTED_ACTIONS:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     [f"{action} is not restricted by this SCP. It does not prevent creating buckets "
                                      "or editing bucket policies; account-level Block Public Access governs whether "
                                      "public access takes effect."], baseline=baseline)
        if not account or account not in context.tree.scopes:
            return RequestEvaluation(RequestImpact.UNKNOWN, ["Request account is missing or unknown."])
        scope = context.tree.scopes[account]
        if scope.is_management_account:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Management account principals are not affected by SCPs."], baseline=baseline)
        if account not in context.target_scope_ids:
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Account is outside the evaluated attachment scope."], baseline=baseline)
        if ptype == "SERVICE_LINKED_ROLE":
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Service-linked roles are not affected by SCPs."], baseline=baseline)
        if not principal:
            return RequestEvaluation(RequestImpact.UNKNOWN, ["Principal ARN not supplied."], baseline=baseline)
        patterns = revision.parameters.get("exempt_principal_arn_patterns", [])
        if any(arn_like(principal, p) for p in patterns):
            return RequestEvaluation(RequestImpact.NOT_DENIED_BY_THIS_CONTROL,
                                     ["Principal matches an ArnNotLike exemption pattern. Not proof of overall "
                                      "authorization."], baseline=baseline)
        reasons = [f"{action} by {principal} is denied by the proposed SCP regardless of the requested values "
                   "(the SCP cannot distinguish strengthening from weakening changes)."]
        if baseline.get("already_denied_by_existing"):
            reasons.append("Already denied by an inherited existing SCP; a lower-scope allow cannot undo it.")
        return RequestEvaluation(RequestImpact.PREDICTED_DENIED, reasons, {"action": action}, baseline)

    def readiness_for_resource(self, resource: ResourceSnapshot, evaluation: ResourceEvaluation,
                               evidence: dict[str, EvidenceView], exception_state: dict[str, Any],
                               context: AssessmentContext) -> tuple[str | None, list[str], list[Blocker]]:
        if evaluation.applicability == Applicability.NOT_APPLICABLE:
            return None, ["Not applicable."], []
        if not evaluation.enforcement_applies:
            return None, ["SCP enforcement does not apply in the management account (coverage gap, not readiness)."], []
        rid, scope, app = resource.resource_id, resource.scope_id, resource.application
        blockers: list[Blocker] = []
        baselines = context.extra.get("account_baselines", {})
        account_state = baselines.get(scope, "UNKNOWN")
        if account_state == "NON_COMPLIANT":
            blockers.append(Blocker(
                "PREREQUISITE_INSECURE", f"{scope}#S3_ACCOUNT_BPA_BASELINE", scope, app,
                "Account-level Block Public Access is not fully enabled. An SCP restricting changes would lock in "
                "the insecure configuration and block remediation by non-exempt principals.",
                "Cloud Engineering establishes the account baseline first (or via an Organizations S3 policy), "
                "then reassess.", "Cloud Engineering"))
        elif account_state == "UNKNOWN":
            blockers.append(Blocker("PREREQUISITE_MISSING", f"{scope}#S3_ACCOUNT_BPA_BASELINE", scope, app,
                                    "Account-level Block Public Access baseline is not verified (missing data).",
                                    "Collect the account configuration with provenance and reassess.",
                                    "Cloud Engineering"))
        if evaluation.configuration_result == ConfigurationResult.NON_COMPLIANT and resource.resource_type == BUCKET_TYPE:
            blockers.append(Blocker("EXISTING_VIOLATION", rid, scope, app,
                                    "Effective Block Public Access is incomplete for this bucket.",
                                    "Remediate before attachment; afterwards only exempt principals could change it.",
                                    app or "Cloud Engineering"))
        if evaluation.configuration_result == ConfigurationResult.UNKNOWN and resource.resource_type == BUCKET_TYPE:
            blockers.append(Blocker("CONFIGURATION_UNKNOWN", rid, scope, app, "Bucket configuration unknown.",
                                    "Collect bucket configuration and reassess.", "Cloud Engineering"))
        if resource.resource_type == ACCOUNT_BPA_TYPE:
            ev = evidence.get("BPA_ADMIN_PROCESS")
            if ev is None:
                blockers.append(Blocker("EVIDENCE_MISSING", f"{scope}#BPA_ADMIN_PROCESS", scope, app,
                                        "No evidence that the exempt administration path and rollback procedure "
                                        "exist for this account.", "Cloud Engineering supplies evidence.",
                                        "Cloud Engineering"))
            elif ev.stale:
                blockers.append(Blocker("EVIDENCE_STALE", f"{scope}#BPA_ADMIN_PROCESS", scope, app,
                                        "Administration-path evidence is stale.", "Refresh the evidence.",
                                        "Cloud Engineering"))
            elif ev.status != "SATISFIED":
                blockers.append(Blocker("EVIDENCE_NOT_SATISFIED", f"{scope}#BPA_ADMIN_PROCESS", scope, app,
                                        f"Administration path not ready: {ev.summary}",
                                        "Complete the procedure and supply evidence.", "Cloud Engineering"))
        if not resource.owner:
            blockers.append(Blocker("MISSING_OWNER", rid, scope, app, "No owner recorded.",
                                    "Record an owner.", "Cloud Security"))
        if any(b.kind in {"PREREQUISITE_INSECURE", "EXISTING_VIOLATION", "EVIDENCE_NOT_SATISFIED"} for b in blockers):
            return Readiness.BLOCKED, [], blockers
        if blockers:
            return Readiness.UNKNOWN, [], blockers
        return Readiness.READY, ["Baseline verified and administration path evidenced."], []

    # ---------------------------------------------------------------- exceptions
    def exception_covers_resource(self, exception: SecurityException, resource: ResourceSnapshot,
                                  tree: ScopeTree) -> bool:
        if exception.granularity == ExceptionGranularity.RESOURCE:
            return resource.resource_id in (exception.resource_ids or [])
        if exception.granularity == ExceptionGranularity.SCOPE:
            return tree.is_within(resource.scope_id, exception.scope_id)
        return False

    def validate_exception_representation(self, exception: SecurityException, revision: ImplementationRevision,
                                          bindings: list[PolicyBinding], tree: ScopeTree,
                                          ring_scope_ids: list[str] | None = None) -> ExceptionRepresentation:
        scope = tree.scopes.get(exception.scope_id)
        if exception.granularity == ExceptionGranularity.RESOURCE:
            return ExceptionRepresentation(
                Representability.REQUIRES_DIFFERENT_MECHANISM,
                "An SCP cannot exempt an individual bucket, and a bucket-level exception cannot override account- or "
                "organization-level Block Public Access. Allowing public access for one bucket would require weakening "
                "the account baseline for every bucket in that account, which is a separate control decision.",
                None, False)
        if scope is None or scope.provider != "aws" or scope.scope_type != ScopeType.AWS_ACCOUNT:
            return ExceptionRepresentation(Representability.UNSUPPORTED,
                                           "AWS exceptions are representable only at account granularity.", None, False)
        actions = PROTECTED_ACTIONS
        principals = exception.principal_patterns or [None]
        inherited = []
        for action in actions:
            for p in principals:
                for h in self._inherited_denies(scope.id, action, p, bindings, tree):
                    if not h["principal_exempt"] and h["management"] == "EXTERNALLY_MANAGED":
                        inherited.append(h)
        if inherited:
            h = inherited[0]
            return ExceptionRepresentation(
                Representability.UNSUPPORTED,
                f"An inherited explicit Deny from existing SCP {h['native_id']} attached at {h['scope_id']} applies. "
                "A lower-scope allow or an exception in this control's SCP cannot undo it; the owner of that policy "
                "(Cloud Engineering) would need to change it.", None, False)
        if exception.granularity == ExceptionGranularity.PRINCIPAL:
            pats = exception.principal_patterns or []
            if not pats or not all(PRINCIPAL_PATTERN.match(p) for p in pats):
                return ExceptionRepresentation(Representability.UNSUPPORTED,
                                               "Principal exceptions need valid IAM role ARN patterns.", None, False)
            return ExceptionRepresentation(
                Representability.REPRESENTABLE,
                "Representable by adding the principal pattern(s) to the SCP ArnNotLike condition through a reviewed "
                "implementation revision. No native expiry: removal needs a Cloud Engineering handoff.",
                {"mechanism": "SCP_CONDITION_ARN_NOT_LIKE", "patterns": pats, "native_expiry": None}, False)
        if ring_scope_ids is not None:
            for rs in ring_scope_ids:
                if rs != scope.id and tree.is_within(scope.id, rs):
                    return ExceptionRepresentation(
                        Representability.REQUIRES_DIFFERENT_MECHANISM,
                        f"The SCP is attached at {rs}, an ancestor of {scope.id}; one account cannot be excluded from "
                        "an OU/root attachment without moving it.", None, False)
        return ExceptionRepresentation(
            Representability.REPRESENTABLE,
            "Representable only by omitting this account from per-account attachments. No native expiry: a removal "
            "handoff to Cloud Engineering is required when the exception ends.",
            {"mechanism": "OMIT_ACCOUNT_ATTACHMENT", "account": scope.native_id, "native_expiry": None}, False)

    # ---------------------------------------------------------------- rings and artifacts
    def validate_ring_settings(self, stage: str, settings: dict[str, Any],
                               revision: ImplementationRevision) -> list[Check]:
        if any(k.lower() in {"effect", "audit", "enforcementmode"} for k in settings):
            return [Check(f"ring.{stage}", "UNSUPPORTED",
                          "SCPs have no audit effect or enforcement mode. Observation is offline assessment only; "
                          "an SCP is never deployed 'in audit mode'.")]
        if stage == RolloutStage.OBSERVATION:
            if settings != {"mode": "OFFLINE_ASSESSMENT"}:
                return [Check(f"ring.{stage}", "UNSUPPORTED",
                              "The only SCP observation setting is {\"mode\": \"OFFLINE_ASSESSMENT\"}.")]
            return [Check(f"ring.{stage}", "PASS", "Offline assessment only; no artifact.")]
        if settings != {"attach": True}:
            return [Check(f"ring.{stage}", "FAIL", "Enforcing SCP rings require {\"attach\": true}.")]
        return [Check(f"ring.{stage}", "PASS", "Attach SCP to ring scopes.")]

    def ring_is_enforcing(self, settings: dict[str, Any]) -> bool:
        return settings.get("attach") is True

    def ring_needs_artifact(self, settings: dict[str, Any]) -> bool:
        return settings.get("attach") is True

    def generate_change_artifacts(self, package: dict[str, Any], revision: ImplementationRevision,
                                  tree: ScopeTree) -> list[Artifact]:
        artifacts: list[Artifact] = []
        control = package["control"]
        doc = revision.native_document
        name = "ccp-" + short_hash(control["id"], revision.implementation_id, revision.content_digest, length=16)
        for ring in package["deploy_rings"]:
            if not self.ring_needs_artifact(ring["settings"]):
                continue
            for scope_id in ring["scope_ids"]:
                scope = tree.get(scope_id)
                content = {
                    "PolicyName": name,
                    "PolicyType": "SERVICE_CONTROL_POLICY",
                    "Description": f"Protects S3 Block Public Access baseline for control {control['id']} "
                                   f"r{control['revision']}",
                    "TargetId": scope.native_id,
                    "PolicyContentDigest": digest(doc),
                    "Content": doc,
                }
                artifacts.append(Artifact(
                    path=f"native/aws/scp-attachment-{scope_id}.json", kind="AWS_SCP_ATTACHMENT",
                    ring_stage=ring["stage"], scope_id=scope_id,
                    native_identity=f"aws-organizations:policy-name/{name}/target/{scope.native_id}",
                    content=content, enforcing=True,
                    notes=["The policy id (p-...) is assigned by AWS Organizations at creation and must be reported "
                           "in the deployment receipt; it is never invented here.",
                           "DEMO-ONLY: AWS documentation not verified from a primary source in this build."]))
        return artifacts

    def compare_observed_state(self, expected: dict[str, Any], observed: dict[str, Any] | None,
                               artifact_kind: str) -> Comparison:
        if observed is None:
            return Comparison("MISSING", ["Attachment not observed."])
        diffs = []
        if expected.get("TargetId") != observed.get("TargetId"):
            diffs.append(f"TargetId: expected {expected.get('TargetId')}, observed {observed.get('TargetId')}")
        observed_digest = digest(observed.get("Content")) if observed.get("Content") is not None else None
        if expected.get("PolicyContentDigest") != observed_digest:
            diffs.append("Policy content digest differs from the approved document.")
        return Comparison("MATCH" if not diffs else "MISMATCH", diffs)

    def mock_observed_state(self, expected: dict[str, Any], scenario: str, artifact_kind: str
                            ) -> dict[str, Any] | None:
        if scenario == "MISSING":
            return None
        observed = copy.deepcopy(expected)
        if scenario == "DRIFT":
            observed["Content"]["Statement"][0]["Action"] = [ACCOUNT_ACTION]
        return observed
