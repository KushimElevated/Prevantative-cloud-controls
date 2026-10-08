"""Complete Azure journey: proposal -> approved export -> mock-verified observation."""

from __future__ import annotations

import io
import zipfile

from journey import (
    AZ_CONTROL,
    AZ_IMPL_REV,
    RETAIL_PARTNER_EXC,
    advance,
    approve,
    assess,
    create_package,
    create_plan,
    resolve_retail_blocker,
    submit_control_and_impl,
    targets,
)


def test_complete_azure_journey(api):
    detail = api.get("cara", f"/controls/{AZ_CONTROL}", expected=200).json()
    assert detail["current_revision"]["status"] == "DRAFT"
    assert detail["next_decision"]["decision"].startswith("Submit control revision")
    # Existing native coverage is mapped: audit-only assignment, externally managed.
    existing = {b["id"]: b for b in detail["existing_bindings"]}
    assert existing["bind-az-existing-audit"]["settings"]["effect"] == "Audit"
    assert existing["bind-az-existing-audit"]["management"] == "EXTERNALLY_MANAGED"

    submit_control_and_impl(api)
    validation = api.get("cara", f"/implementation-revisions/{AZ_IMPL_REV}", expected=200).json()["latest_validation"]
    assert validation["integration_ready"] is True
    assert validation["possible_duplicates"][0]["binding_id"] == "bind-az-existing-audit"

    run = assess(api)
    rollup = run["rollup"]
    assert rollup["baseline_vs_proposed"]["baseline_coverage_counts"] == {"AUDIT_ONLY": 7}
    pilot_blockers = [b for b in run["blockers"] if b["scope_id"] == "az-rg-retail-search"]
    assert {b["kind"] for b in pilot_blockers} == {"EVIDENCE_MISSING", "EXCEPTION_NOT_APPLIED"}

    plan = create_plan(api)
    pkg = create_package(api, plan["id"])
    failing = {g["id"] for g in pkg["gate_results"] if g["status"] == "FAIL"}
    assert "readiness" in failing  # retail-catalog has no client connectivity evidence yet
    approve(api, pkg, "sam", "SECURITY_APPROVER", expected=409)

    resolve_retail_blocker(api)
    run2 = assess(api)
    assert not [b for b in run2["blockers"] if b["kind"] == "EVIDENCE_MISSING" and b["scope_id"] == "az-rg-retail-search"]
    pkg2 = create_package(api, plan["id"])
    assert pkg2["package_revision"] == 2
    assert pkg2["manifest_digest"] != pkg["manifest_digest"]
    assert [a["exception_id"] for a in pkg2["manifest"]["exception_set"]["additions"]] == [RETAIL_PARTNER_EXC]
    assert pkg2["manifest"]["exception_set"]["expiry_consequences"][0]["exception_id"] == RETAIL_PARTNER_EXC
    pre = [g for g in pkg2["gate_results"] if g["phase"] == "PRE_APPROVAL" and g["status"] != "PASS"]
    assert pre == []
    old = api.get("cara", f"/change-packages/{pkg['id']}", expected=200).json()
    assert old["status"] == "SUPERSEDED"

    approve(api, pkg2, "sam", "SECURITY_APPROVER")
    approved = approve(api, pkg2, "eli", "CLOUD_ENGINEER").json()
    assert approved["status"] == "APPROVED"
    detail = api.get("cara", f"/controls/{AZ_CONTROL}", expected=200).json()
    assert detail["current_revision"]["status"] == "APPROVED"

    bundle = api.post("eli", f"/change-packages/{pkg2['id']}/export", expected=201).json()
    assert bundle["kind"] == "APPROVED_HANDOFF"
    full = api.get("eli", f"/handoff-bundles/{bundle['id']}", expected=200).json()
    files = full["files"]
    for required in ("manifest.json", "parameters.json", "scopes.json", "baseline-comparison.json",
                     "assessment-summary.json", "exceptions.json", "approvals/attestations.json", "REVIEWERS.md",
                     "rollout.json", "ROLLBACK.md", "PR_DESCRIPTION.md", "receipt-schema.json"):
        assert required in files, required
    assert any(p.startswith("native/azure/policy-assignment-") for p in files)
    assert any(p.startswith("native/azure/policy-exemption-") for p in files)
    assert "APPROVED FOR HANDOFF" in files["README.md"]
    assert "policyDefinitions/ee980b6d-0eca-4501-8d54-f6290fd512c3" in files[
        "native/azure/policy-assignment-az-sub-retail-prod.json"]
    assert all(t["state"] == "EXPORTED" for t in full["delivery_targets"])

    # Re-export is deterministic: same bundle, same bytes.
    again = api.post("eli", f"/change-packages/{pkg2['id']}/export", expected=201).json()
    assert again["id"] == bundle["id"] and again["bundle_digest"] == bundle["bundle_digest"]
    z1 = api.get("eli", f"/handoff-bundles/{bundle['id']}/download", expected=200).content
    z2 = api.get("eli", f"/handoff-bundles/{bundle['id']}/download", expected=200).content
    assert z1 == z2
    assert "manifest.json" in zipfile.ZipFile(io.BytesIO(z1)).namelist()

    advance(api, plan["id"], "OBSERVATION")
    advance(api, plan["id"], "PILOT")
    out = api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "SUCCESS"},
                   expected=200).json()
    assert out["provenance"] == "MOCK"
    assert {r["validation_status"] for r in out["receipts"]} == {"VALID"}
    ts = targets(api, bundle["id"])
    assert {t["state"] for t in ts} == {"APPLIED"}  # a pipeline success is not verification

    for t in ts:
        res = api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "MATCH"},
                       expected=201).json()
        assert res["observation"]["provenance"] == "FIXTURE"
        assert res["delivery_target"]["state"] == "VERIFIED"

    exc = api.get("cara", f"/exceptions/{RETAIL_PARTNER_EXC}", expected=200).json()
    assert exc["disposition"] == "EFFECTIVE" and exc["native_status"] == "APPLIED"
    detail = api.get("cara", f"/controls/{AZ_CONTROL}", expected=200).json()
    cov = detail["coverage"]
    assert cov["verified_protected_resources"] == 1
    assert cov["effective_exemptions"] == 2
    assert cov["verified_protected_ratio"]["denominator"] == cov["known_applicable_resources"] == 7
    bindings = {b["origin"] for b in detail["existing_bindings"]}
    assert bindings == {"EXISTING_IMPORTED", "DELIVERED_FROM_PACKAGE"}  # audit at MG, deny at pilot sub

    metrics = api.get("vic", "/dashboard/metrics", expected=200).json()
    assert metrics["delivery"]["targets_by_state"] == {"VERIFIED": 2}
    assert metrics["coverage_pairs"]["unit"].startswith("control-resource pairs")
    assert all(m["status"] == "UNAVAILABLE" for m in metrics["outcome_metrics"])

    audit = api.get("vic", f"/audit?control_id={AZ_CONTROL}&limit=200", expected=200).json()["items"]
    actions = {a["action"] for a in audit}
    for a in ("control_revision.submitted", "assessment.completed", "change_package.submitted",
              "change_package.decision.approve", "handoff.exported", "rollout.advanced",
              "delivery.observation_recorded"):
        assert a in actions, a


def test_draft_bundle_is_labelled_unapproved(api):
    submit_control_and_impl(api)
    assess(api)
    plan = create_plan(api)
    pkg = create_package(api, plan["id"])
    api.post("eli", f"/change-packages/{pkg['id']}/export", expected=409)  # not approved
    draft = api.post("cara", f"/change-packages/{pkg['id']}/draft-export", expected=201).json()
    assert draft["kind"] == "DRAFT_UNAPPROVED"
    files = api.get("cara", f"/handoff-bundles/{draft['id']}", expected=200).json()["files"]
    assert "DRAFT - UNAPPROVED - NOT FOR DEPLOYMENT" in files["README.md"]
    assert '"status": "UNAPPROVED"' in files["approvals/attestations.json"]
    # A draft bundle is never deployable.
    r = api.post("eli", "/receipts", {
        "receipt_id": "draft-1", "provenance": "MOCK", "bundle_digest": draft["bundle_digest"], "ring": "PILOT",
        "target_scope_native_id": "/subscriptions/00000000-0000-0000-0000-000000000101",
        "pipeline_run_ref": "x", "result": "APPLIED", "applied_native_identities": [],
        "started_at": "2026-10-01T12:00:00Z", "completed_at": "2026-10-01T12:10:00Z"}, expected=200).json()
    assert r["validation_status"] == "REJECTED_NOT_AUTHORIZED"


def test_propose_new_control_from_recurring_risk(api):
    body = {
        "id": "CTL-AZ-SEARCH-PNA-EU", "name": "EU AI Search public access disabled",
        "description": "EU-scoped variant.", "security_objective": "No public network access for EU search services.",
        "rationale": "Recurring findings.", "source_evidence": [
            {"kind": "WIZ_ISSUE", "reference": "fixture://wiz/x", "summary": "Recurring finding"}],
        "severity": "HIGH", "providers": ["azure"], "resource_types": ["Microsoft.Search/searchServices"],
        "applicability_criteria": "EU subscriptions", "security_owner": "Cloud Security",
        "engineering_owner": "Cloud Engineering", "framework_refs": [], "exception_eligible": True,
        "prevention_boundary": "ARM create/update only.", "limitations": ["Existing resources need remediation."],
        "change_reason": "Recurring risk",
    }
    created = api.post("cara", "/controls", body, expected=201).json()
    assert created["current_revision"]["status"] == "DRAFT"
    impl = api.post("cara", "/controls/CTL-AZ-SEARCH-PNA-EU/implementations", {
        "provider": "azure", "name": "Built-in assignment", "mechanism_role": "PRIMARY_GUARDRAIL",
        "policy_kind": "AZURE_POLICY_BUILTIN_ASSIGNMENT", "source_kind": "BUILT_IN",
        "source_ref": "/providers/Microsoft.Authorization/policyDefinitions/ee980b6d-0eca-4501-8d54-f6290fd512c3",
        "pinned_version": "1.0.1", "parameters": {"effect": "Deny"},
        "assignment_settings": {"enforcementMode": "Default"}, "change_reason": "initial"}, expected=201).json()
    rev = impl["revision"]
    assert rev["verification"]["status"] == "VERIFIED_PRIMARY_SOURCE"  # decided server-side
    assert rev["native_document"]["name"] == "ee980b6d-0eca-4501-8d54-f6290fd512c3"  # verified copy pinned
    v = api.post("cara", f"/implementation-revisions/{rev['id']}/validate", expected=200).json()
    assert v["outcome"] == "PASS"
    # Drafts are editable; unreferenced drafts can be deleted.
    crev = created["current_revision"]
    out = api.call("cara", "DELETE", f"/control-revisions/{crev['id']}", expected=409).json()
    assert "referenced" in out["error"]["message"]
