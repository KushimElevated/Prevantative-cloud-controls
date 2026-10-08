"""AWS SCP example: multiple mechanisms, prerequisites, inherited deny, no audit mode."""

from __future__ import annotations

AWS_REV = "irev-aws-scp-bpa-1"
CTL = "CTL-AWS-S3-PUBLIC-ACCESS"


def _assess(api):
    return api.post("cara", "/assessments", {"implementation_revision_id": AWS_REV, "target_scope_id": "aws-root"},
                    expected=201).json()


def _plan(rings, title="SCP plan"):
    return {"control_id": CTL, "title": title, "implementation_revision_id": AWS_REV, "target_scope_id": "aws-root",
            "rings": rings, "pause_criteria": ["Unexpected AccessDenied on Block Public Access administration"],
            "rollback_plan": {"prior_known_good": "No attachment of this SCP",
                              "steps": ["Detach the SCP via a reviewed change using the exempt admin role"],
                              "limitations": ["Detaching does not restore Block Public Access settings"],
                              "emergency_path": "Cloud Engineering break-glass for AWS Organizations"},
            "prerequisite_changes": [], "deployment_instructions": "Attach after baseline verified."}


def test_prerequisite_failures_block_readiness(api):
    run = _assess(api)
    kinds = {(b["kind"], b["scope_id"]) for b in run["blockers"]}
    assert ("PREREQUISITE_INSECURE", "aws-acct-payments") in kinds
    assert ("PREREQUISITE_MISSING", "aws-acct-sandbox") in kinds
    rows = {r["subject_name"]: r for r in api.get(
        "cara", f"/assessments/{run['id']}/results?subject_kind=RESOURCE&limit=100", expected=200).json()["items"]}
    assert rows["retail-prod account BPA"]["readiness"] == "READY"
    assert rows["payments-prod account BPA"]["configuration_result"] == "NON_COMPLIANT"
    assert rows["payments-prod account BPA"]["readiness"] == "BLOCKED"
    assert rows["sandbox account BPA"]["configuration_result"] == "UNKNOWN"
    # Account-level protection makes an unconfigured bucket effectively protected.
    assert rows["retail-public-site-fixture"]["configuration_result"] == "COMPLIANT"
    assert rows["analytics--usw2-az1--x-s3"]["configuration_result"] == "NOT_APPLICABLE"
    # The management account is outside SCP enforcement: a coverage gap, not readiness.
    assert rows["management account BPA"]["readiness"] is None
    assert rows["management account BPA"]["evidence"]["enforcement_applies"] is False


def test_request_semantics_and_inherited_deny(api):
    run = _assess(api)
    rows = {r["subject_id"]: r for r in api.get(
        "cara", f"/assessments/{run['id']}/results?subject_kind=REQUEST", expected=200).json()["items"]}
    assert rows["REQ-AWS-1"]["request_impact"] == "PREDICTED_DENIED"
    assert rows["REQ-AWS-2"]["request_impact"] == "NOT_DENIED_BY_THIS_CONTROL"  # exempt admin role
    assert rows["REQ-AWS-3"]["request_impact"] == "NOT_DENIED_BY_THIS_CONTROL"  # management account
    assert rows["REQ-AWS-4"]["request_impact"] == "NOT_DENIED_BY_THIS_CONTROL"  # CreateBucket not restricted
    assert "does not prevent creating buckets" in rows["REQ-AWS-4"]["reasons"][0]
    assert rows["REQ-AWS-5"]["request_impact"] == "NOT_DENIED_BY_THIS_CONTROL"  # service-linked role
    assert rows["REQ-AWS-6"]["request_impact"] == "UNKNOWN"
    assert rows["REQ-AWS-7"]["request_impact"] == "PREDICTED_DENIED"
    assert rows["REQ-AWS-7"]["baseline"]["already_denied_by_existing"] is True
    assert any("lower-scope allow cannot undo" in r for r in rows["REQ-AWS-7"]["reasons"])


def test_scp_audit_mode_is_unsupported(api):
    r = api.post("cara", "/rollout-plans", _plan([
        {"stage": "OBSERVATION", "scope_ids": ["aws-ou-prod"], "settings": {"effect": "Audit"}}]), expected=422).json()
    assert r["error"]["code"] == "UNSUPPORTED"
    assert "no audit effect" in r["error"]["message"]
    impl = api.get("cara", "/implementations/impl-aws-scp-bpa", expected=200).json()
    rev = impl["revisions"][-1]
    body = {k: rev[k] for k in ("policy_kind", "source_kind", "source_ref", "pinned_version", "assignment_settings",
                                "native_document", "prerequisites", "limitations")}
    new = api.post("cara", "/implementations/impl-aws-scp-bpa/revisions",
                   {**body, "parameters": {**rev["parameters"], "effect": "Audit"}, "change_reason": "audit"},
                   expected=201).json()
    v = api.post("cara", f"/implementation-revisions/{new['id']}/validate", expected=200).json()
    assert v["outcome"] == "UNSUPPORTED"


def test_unrepresentable_exceptions(api):
    bucket = api.post("riley", "/exceptions", {
        "control_id": CTL, "application": "retail-web", "team": "Retail Digital",
        "business_justification": "Marketing site serves static assets publicly from S3.",
        "technical_justification": "Static website hosting from a single bucket.",
        "scope_id": "aws-acct-retail", "granularity": "RESOURCE",
        "resource_ids": ["arn:aws:s3:::retail-public-site-fixture"], "risk_owner": "Retail CTO",
        "compensating_controls": ["Only public assets stored"], "expires_at": "2026-11-15T00:00:00Z"},
        expected=201).json()
    assert bucket["representability"] == "REQUIRES_DIFFERENT_MECHANISM"
    assert "cannot override account" in bucket["representability_reason"]
    assert bucket["native_expiry_supported"] is False
    api.post("sam", f"/exceptions/{bucket['id']}/start-review", {"expected_lock_version": 1}, expected=200)
    r = api.post("sam", f"/exceptions/{bucket['id']}/decision", {
        "decision": "APPROVE", "rationale": "Would like to approve", "expected_lock_version": 2}, expected=409).json()
    assert r["error"]["code"] == "REQUIRES_DIFFERENT_MECHANISM"
    rejected = api.post("sam", f"/exceptions/{bucket['id']}/decision", {
        "decision": "REJECT", "rationale": "Use a different delivery pattern", "expected_lock_version": 2},
        expected=200).json()
    assert rejected["governance_status"] == "REJECTED"
    assert [d["decision"] for d in rejected["decisions"]] == ["REJECT"]

    seeded = api.get("cara", "/exceptions/exc-aws-payments-account", expected=200).json()
    assert seeded["representability"] == "UNSUPPORTED"
    assert "inherited explicit Deny" in seeded["representability_reason"]


def test_aws_package_is_blocked_and_demo_only(api):
    _assess(api)
    plan = api.post("cara", "/rollout-plans", _plan([
        {"stage": "OBSERVATION", "scope_ids": ["aws-root"], "settings": {"mode": "OFFLINE_ASSESSMENT"}},
        {"stage": "PILOT", "scope_ids": ["aws-acct-payments"], "settings": {"attach": True}}]), expected=201).json()
    pkg = api.post("cara", f"/rollout-plans/{plan['id']}/packages", {"through_stage": "PILOT"}, expected=201).json()
    failing = {g["id"]: g for g in pkg["gate_results"] if g["status"] == "FAIL"}
    assert "integration_verified" in failing
    assert "readiness" in failing
    assert any("PREREQUISITE_INSECURE" in b for b in failing["readiness"]["detail"]["open_blockers"])
    assert "exceptions_representable" in failing
    r = api.post("sam", f"/change-packages/{pkg['id']}/decisions", {
        "decision": "APPROVE", "role": "SECURITY_APPROVER", "expected_digest": pkg["manifest_digest"],
        "rationale": "attempt despite gates"}, expected=409).json()
    assert r["error"]["code"] == "GATE_FAILED"
    draft = api.post("cara", f"/change-packages/{pkg['id']}/draft-export", expected=201).json()
    files = api.get("cara", f"/handoff-bundles/{draft['id']}", expected=200).json()["files"]
    assert not any(p.startswith("native/aws/") and "audit" in p.lower() for p in files)
    assert "exempt administrative role" in files["ROLLBACK.md"]
    assert "DRAFT - UNAPPROVED" in files["README.md"]


def test_organizations_s3_policy_is_recorded_only_as_alternative(api):
    detail = api.get("cara", f"/controls/{CTL}", expected=200).json()
    assert any("Organizations S3 policies" in l for l in detail["current_revision"]["limitations"])
    assert {i["implementation"]["provider"] for i in detail["implementations"]} == {"aws"}
    assert len(detail["implementations"]) == 1
