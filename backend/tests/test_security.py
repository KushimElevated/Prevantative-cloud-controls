"""Authorization, separation of duties, gate enforcement, configuration safety and audit integrity."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.config import ConfigurationError, Settings, validate_settings
from journey import (
    AZ_CONTROL_REV,
    advance,
    approve,
    approved_package,
    assess,
    create_package,
    create_plan,
    resolve_retail_blocker,
    submit_control_and_impl,
)


def _ready_package(api):
    submit_control_and_impl(api)
    resolve_retail_blocker(api)
    assess(api)
    plan = create_plan(api)
    return plan, create_package(api, plan["id"])


def test_self_approval_and_multi_role_identity(api):
    plan, pkg = _ready_package(api)
    # Grant cara (the author) security approver authority: still cannot approve her own change.
    api.post("ada", "/admin/role-assignments", {"user_id": "u-cara", "role": "SECURITY_APPROVER"}, expected=201)
    r = approve(api, pkg, "cara", "SECURITY_APPROVER", expected=403).json()
    assert r["error"]["code"] == "SELF_APPROVAL"
    # One identity holding both approver roles cannot satisfy both approvals.
    approve(api, pkg, "max", "SECURITY_APPROVER", expected=200)
    r = approve(api, pkg, "max", "CLOUD_ENGINEER", expected=403).json()
    assert r["error"]["code"] == "SEPARATION_OF_DUTIES"
    assert api.get("cara", f"/change-packages/{pkg['id']}", expected=200).json()["status"] == "IN_REVIEW"
    # Denials are audited even though the command transaction rolled back.
    audit = api.get("vic", "/audit?action=command.denied&limit=50", expected=200).json()["items"]
    assert {a["details"]["code"] for a in audit} >= {"SELF_APPROVAL", "SEPARATION_OF_DUTIES"}


def test_admin_cannot_bypass_or_self_grant(api):
    plan, pkg = _ready_package(api)
    r = approve(api, pkg, "ada", "SECURITY_APPROVER", expected=403).json()
    assert r["error"]["code"] == "FORBIDDEN"
    r = api.post("ada", "/admin/role-assignments", {"user_id": "u-ada", "role": "SECURITY_APPROVER"},
                 expected=403).json()
    assert r["error"]["code"] == "SELF_GRANT"
    api.post("ada", f"/change-packages/{pkg['id']}/export", expected=403)
    api.post("ada", f"/rollout-plans/{plan['id']}/advance",
             {"to_stage": "OBSERVATION", "expected_lock_version": 1, "reason": "bypass"}, expected=403)


def test_direct_api_calls_cannot_skip_gates(api, client):
    plan, pkg = _ready_package(api)
    # No approval yet: export, advance and receipts are all refused by the backend.
    api.post("eli", f"/change-packages/{pkg['id']}/export", expected=409)
    advance(api, plan["id"], "OBSERVATION", expected=409)
    # There is no generic status PATCH endpoint.
    headers = api.login("eli")
    assert client.patch(f"/api/v1/change-packages/{pkg['id']}", json={"status": "APPROVED"},
                        headers=headers).status_code == 405
    assert client.patch(f"/api/v1/rollout-plans/{plan['id']}", json={"stage": "BROAD"},
                        headers=headers).status_code == 405
    # Unknown fields are rejected rather than silently applied.
    r = api.post("cara", f"/rollout-plans/{plan['id']}/packages", {"through_stage": "PILOT", "status": "APPROVED"},
                 expected=422).json()
    assert r["error"]["code"] == "REQUEST_INVALID"
    # Rings cannot be skipped even with an approved package.
    approve(api, pkg, "sam", "SECURITY_APPROVER")
    approve(api, pkg, "eli", "CLOUD_ENGINEER")
    advance(api, plan["id"], "PILOT", expected=409)


def test_unauthorised_users_cannot_access_other_scopes(api):
    # Riley (retail) cannot see a payments exception, or request one there.
    api.get("riley", "/exceptions/exc-az-payments-legacy", expected=404)
    api.get("riley", "/exceptions/exc-az-retail-partner", expected=200)
    listed = {e["id"] for e in api.get("riley", "/exceptions?limit=200", expected=200).json()["items"]}
    assert "exc-az-payments-legacy" not in listed and "exc-az-retail-partner" in listed
    api.post("riley", "/exceptions", {
        "control_id": "CTL-AZ-SEARCH-PNA", "application": "payments-kb", "team": "Payments",
        "business_justification": "Trying to request outside my authorised scope.",
        "technical_justification": "This must be refused by the server.", "scope_id": "az-rg-payments-search",
        "granularity": "SCOPE", "risk_owner": "x", "compensating_controls": ["none"],
        "expires_at": "2026-11-01T00:00:00Z"}, expected=403)
    # Assessment results are filtered to authorised scopes; the cross-scope rollup is redacted.
    run = assess(api)
    rows = api.get("riley", f"/assessments/{run['id']}/results?limit=200", expected=200).json()["items"]
    assert rows and all(r["scope_id"] in ("az-rg-retail-search", "az-sub-retail-prod") for r in rows)
    view = api.get("riley", f"/assessments/{run['id']}", expected=200).json()
    assert "redacted" in view["rollup"]
    api.get("riley", "/scopes/az-sub-payments-prod", expected=404)
    # Application ownership comes from the server, not the client.
    api.post("riley", "/exceptions", {
        "control_id": "CTL-AZ-SEARCH-PNA", "application": "retail-catalog", "team": "Retail Digital",
        "business_justification": "Claiming a resource that belongs to another application.",
        "technical_justification": "Server must compare against recorded ownership.",
        "scope_id": "az-rg-retail-search", "granularity": "RESOURCE",
        "resource_ids": ["/subscriptions/00000000-0000-0000-0000-000000000101/resourceGroups/rg-retail-search/"
                         "providers/Microsoft.Search/searchServices/srch-retail-partner"],
        "risk_owner": "x", "compensating_controls": ["none"], "expires_at": "2026-11-01T00:00:00Z"}, expected=403)


def test_authentication_is_required_and_tokens_verified(client, api):
    assert client.get("/api/v1/controls").status_code == 401
    assert client.get("/api/v1/controls", headers={"Authorization": "Bearer abc.def"}).status_code == 401
    token = api.login("vic")["Authorization"].split(" ", 1)[1]
    body, sig = token.split(".")
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    assert client.get("/api/v1/controls", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    # A client-supplied role header is ignored.
    r = client.post("/api/v1/assessments", headers={"Authorization": f"Bearer {token}", "X-Role": "CONTROL_ENGINEER"},
                    json={"implementation_revision_id": "irev-az-search-pna-1", "target_scope_id": "az-mg-contoso"})
    assert r.status_code == 403


def test_request_limits(client, api):
    headers = api.login("cara")
    big = {"change_reason": "x" * 300_000}
    r = client.post("/api/v1/controls/CTL-AZ-SEARCH-PNA/revisions", json=big, headers=headers)
    assert r.status_code == 413
    r = client.post("/api/v1/controls/CTL-AZ-SEARCH-PNA/revisions", content=b"change_reason=x",
                    headers={**headers, "content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    assert r.headers.get("x-correlation-id")


@pytest.mark.parametrize("overrides,message", [
    ({"app_env": "production", "auth_mode": "local-demo", "secret_key": "x"}, "only permitted"),
    ({"app_env": "production", "auth_mode": "oidc", "secret_key": "x"}, "not implemented"),
    ({"enable_live_deployment": True}, "not supported"),
    ({"handoff_adapter": "github-pr"}, "not implemented"),
    ({"pipeline_mode": "live"}, "not implemented"),
    ({"cors_allowed_origins": "*"}, "Wildcard"),
])
def test_unsafe_configuration_fails_startup(overrides, message):
    with pytest.raises(ConfigurationError, match=message):
        validate_settings(Settings(**overrides))


def test_demo_login_unavailable_outside_local_mode(app, client):
    app.state.settings.app_env = "production"
    try:
        assert client.post("/api/v1/auth/dev-login", json={"username": "cara"}).status_code == 403
        assert client.get("/api/v1/auth/demo-users").status_code == 403
    finally:
        app.state.settings.app_env = "test"


def test_audit_is_append_only_for_app_role(connection):
    connection.execute(text("SAVEPOINT s1"))
    with pytest.raises(DBAPIError, match="permission denied"):
        connection.execute(text("UPDATE audit_events SET action = 'tampered'"))
    connection.execute(text("ROLLBACK TO SAVEPOINT s1"))
    with pytest.raises(DBAPIError, match="permission denied"):
        connection.execute(text("DELETE FROM audit_events"))
    connection.execute(text("ROLLBACK TO SAVEPOINT s1"))
    with pytest.raises(DBAPIError, match="permission denied"):
        connection.execute(text("DELETE FROM approval_decisions"))
    connection.execute(text("ROLLBACK TO SAVEPOINT s1"))


def test_submitted_revisions_are_immutable(api, connection):
    api.post("cara", f"/control-revisions/{AZ_CONTROL_REV}/submit", {"expected_lock_version": 1}, expected=200)
    r = api.put("cara", f"/control-revisions/{AZ_CONTROL_REV}", {
        "name": "changed", "description": "d", "security_objective": "o", "rationale": "r", "source_evidence": [],
        "severity": "LOW", "providers": ["azure"], "resource_types": ["x"], "applicability_criteria": "a",
        "security_owner": "s", "engineering_owner": "e", "framework_refs": [], "exception_eligible": False,
        "prevention_boundary": "p", "limitations": [], "change_reason": "edit", "expected_lock_version": 2},
        expected=409).json()
    assert r["error"]["code"] == "IMMUTABLE_REVISION"
    connection.execute(text("SAVEPOINT s2"))
    with pytest.raises(DBAPIError, match="immutable"):
        connection.execute(text("UPDATE control_revisions SET name = 'tampered' WHERE id = :i"),
                           {"i": AZ_CONTROL_REV})
    connection.execute(text("ROLLBACK TO SAVEPOINT s2"))
    with pytest.raises(DBAPIError, match="append-only"):
        connection.execute(text("UPDATE resource_snapshots SET name = 'x'"))
    connection.execute(text("ROLLBACK TO SAVEPOINT s2"))


def test_optimistic_concurrency(api):
    r = api.post("cara", f"/control-revisions/{AZ_CONTROL_REV}/submit", {"expected_lock_version": 99},
                 expected=409).json()
    assert r["error"]["code"] == "STALE_VERSION"
    plan, pkg = approved_package(api)
    r = api.post("eli", f"/rollout-plans/{plan['id']}/advance",
                 {"to_stage": "OBSERVATION", "expected_lock_version": 999, "reason": "x"}, expected=409).json()
    assert r["error"]["code"] == "STALE_VERSION"
