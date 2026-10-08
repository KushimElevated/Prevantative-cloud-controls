"""Reusable steps of the Azure AI Search journey, driven only through the public API."""

from __future__ import annotations

AZ_CONTROL = "CTL-AZ-SEARCH-PNA"
AZ_IMPL_REV = "irev-az-search-pna-1"
AZ_CONTROL_REV = "crev-az-search-pna-1"
RETAIL_PARTNER_EXC = "exc-az-retail-partner"


def submit_control_and_impl(api) -> None:
    api.post("cara", f"/control-revisions/{AZ_CONTROL_REV}/submit", {"expected_lock_version": 1}, expected=200)
    v = api.post("cara", f"/implementation-revisions/{AZ_IMPL_REV}/validate", expected=200).json()
    assert v["outcome"] == "PASS", v
    api.post("cara", f"/implementation-revisions/{AZ_IMPL_REV}/submit", {"expected_lock_version": 1}, expected=200)


def assess(api, target: str = "az-mg-contoso", **extra) -> dict:
    body = {"implementation_revision_id": AZ_IMPL_REV, "target_scope_id": target, **extra}
    return api.post("cara", "/assessments", body, expected=201).json()


def resolve_retail_blocker(api) -> None:
    api.post("riley", "/readiness-evidence", {
        "control_id": AZ_CONTROL, "application": "retail-catalog", "scope_id": "az-sub-retail-prod",
        "prerequisite": "CLIENT_CONNECTIVITY", "status": "SATISFIED",
        "summary": "Storefront validated against the private endpoint.", "evidence_ref": "https://tickets.example/1"},
        expected=201)


def plan_body(pilot: str = "az-sub-retail-prod") -> dict:
    return {
        "control_id": AZ_CONTROL, "title": "AI Search PNA rollout", "implementation_revision_id": AZ_IMPL_REV,
        "target_scope_id": "az-mg-contoso",
        "rings": [
            {"stage": "OBSERVATION", "scope_ids": ["az-mg-contoso"],
             "settings": {"use_existing_binding_id": "bind-az-existing-audit"}},
            {"stage": "PILOT", "scope_ids": [pilot], "settings": {"effect": "Deny", "enforcementMode": "Default"}},
            {"stage": "LIMITED", "scope_ids": ["az-sub-payments-prod"],
             "settings": {"effect": "Deny", "enforcementMode": "Default"}},
        ],
        "pause_criteria": ["Unexpected denies reported by an application owner"],
        "rollback_plan": {"prior_known_good": "Audit-only assignment at contoso-root (baseline)",
                          "steps": ["Remove the pilot Deny assignment via reviewed change"],
                          "limitations": ["Does not undo application changes"],
                          "emergency_path": "Cloud Engineering break-glass change procedure"},
        "prerequisite_changes": [],
        "deployment_instructions": "Apply exemptions before the Deny assignment.",
    }


def create_plan(api, **kw) -> dict:
    return api.post("cara", "/rollout-plans", plan_body(**kw), expected=201).json()


def create_package(api, plan_id: str, through: str = "PILOT") -> dict:
    return api.post("cara", f"/rollout-plans/{plan_id}/packages", {"through_stage": through}, expected=201).json()


def approve(api, pkg: dict, user: str, role: str, expected=200):
    return api.post(user, f"/change-packages/{pkg['id']}/decisions", {
        "decision": "APPROVE", "role": role, "expected_digest": pkg["manifest_digest"],
        "rationale": f"{role} review of the exact package"}, expected=expected)


def approved_package(api) -> tuple[dict, dict]:
    submit_control_and_impl(api)
    resolve_retail_blocker(api)
    assess(api)
    plan = create_plan(api)
    pkg = create_package(api, plan["id"])
    approve(api, pkg, "sam", "SECURITY_APPROVER")
    out = approve(api, pkg, "eli", "CLOUD_ENGINEER").json()
    assert out["status"] == "APPROVED", out
    return plan, out


def advance(api, plan_id: str, stage: str, expected=200):
    lv = api.get("eli", f"/rollout-plans/{plan_id}", expected=200).json()["lock_version"]
    return api.post("eli", f"/rollout-plans/{plan_id}/advance",
                    {"to_stage": stage, "expected_lock_version": lv, "reason": f"advance to {stage}"},
                    expected=expected)


def exported_and_piloted(api) -> tuple[dict, dict, dict]:
    plan, pkg = approved_package(api)
    bundle = api.post("eli", f"/change-packages/{pkg['id']}/export", expected=201).json()
    advance(api, plan["id"], "OBSERVATION")
    advance(api, plan["id"], "PILOT")
    return plan, pkg, bundle


def targets(api, bundle_id: str) -> list[dict]:
    return api.get("eli", f"/handoff-bundles/{bundle_id}", expected=200).json()["delivery_targets"]
