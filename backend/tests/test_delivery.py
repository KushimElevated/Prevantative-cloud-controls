"""Receipts, observations, drift, staleness, material change and expiry."""

from __future__ import annotations

from datetime import timedelta

from journey import (
    RETAIL_PARTNER_EXC,
    advance,
    approve,
    approved_package,
    create_package,
    exported_and_piloted,
    targets,
)


def _receipt(bundle, target_native, result="APPLIED", ids=None, completed="2026-10-01T12:20:00Z", key="r-1"):
    return {"receipt_id": key, "provenance": "MOCK", "bundle_digest": bundle["bundle_digest"], "ring": "PILOT",
            "target_scope_native_id": target_native, "pipeline_run_ref": "MOCK-RUN-1", "result": result,
            "applied_native_identities": ids or [], "started_at": "2026-10-01T12:00:00Z", "completed_at": completed}


SUB = "/subscriptions/00000000-0000-0000-0000-000000000101"


def _assignment(api, bundle):
    return next(t for t in targets(api, bundle["id"]) if t["artifact_kind"] == "AZURE_POLICY_ASSIGNMENT")


def test_duplicate_and_conflicting_receipts(api):
    plan, pkg, bundle = exported_and_piloted(api)
    t = _assignment(api, bundle)
    body = _receipt(bundle, SUB, ids=[{"native_identity": t["native_identity"], "config_digest": t["expected_digest"]}])
    first = api.post("eli", "/receipts", body, expected=200).json()
    assert first["validation_status"] == "VALID" and first["duplicate"] is False
    again = api.post("eli", "/receipts", body, expected=200).json()
    assert again["duplicate"] is True and again["id"] == first["id"]
    conflicting = {**body, "result": "FAILED"}
    assert api.post("eli", "/receipts", conflicting, expected=409).json()["error"]["code"] == "RECEIPT_CONFLICT"
    assert _assignment(api, bundle)["state"] == "APPLIED"


def test_failed_stale_and_mismatched_receipts_never_verify(api):
    plan, pkg, bundle = exported_and_piloted(api)
    t = _assignment(api, bundle)
    good_ids = [{"native_identity": t["native_identity"], "config_digest": t["expected_digest"]}]
    # Mismatched digest: rejected, follow-up work created, state unchanged.
    bad = api.post("eli", "/receipts", _receipt(bundle, SUB, ids=[{**good_ids[0], "config_digest": "sha256:other"}],
                                                key="mm"), expected=200).json()
    assert bad["validation_status"] == "REJECTED_DIGEST_MISMATCH"
    # Wrong scope.
    wrong = api.post("eli", "/receipts", _receipt(bundle, "/subscriptions/00000000-0000-0000-0000-000000000102",
                                                  ids=good_ids, key="ws"), expected=200).json()
    assert wrong["validation_status"] == "REJECTED_SCOPE_MISMATCH"
    # Stale: completed outside the freshness window.
    stale = api.post("eli", "/receipts", _receipt(bundle, SUB, ids=good_ids, completed="2026-09-29T00:00:00Z",
                                                  key="st"), expected=200).json()
    assert stale["validation_status"] == "STALE"
    assert _assignment(api, bundle)["state"] == "EXPORTED"
    # A matching observation cannot verify without a valid APPLIED receipt.
    obs = api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "MATCH"},
                   expected=201).json()
    assert obs["delivery_target"]["state"] == "EXPORTED"
    # Failed receipt: FAILED state and work item, never verified.
    failed = api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "FAILURE"},
                      expected=200).json()
    assert {r["validation_status"] for r in failed["receipts"]} == {"VALID"}
    assert _assignment(api, bundle)["state"] == "FAILED"
    work = api.get("eli", "/work-items?kind=DELIVERY_FAILURE&limit=50", expected=200).json()["items"]
    assert work
    # Live receipts are refused outright.
    live = {**_receipt(bundle, SUB, ids=good_ids, key="live"), "provenance": "LIVE"}
    assert api.post("eli", "/receipts", live, expected=422).json()["error"]["code"] == "UNSUPPORTED"
    # The mock pipeline's own STALE and MISMATCH scenarios are rejected too.
    for scenario, status in (("STALE", "STALE"), ("MISMATCH", "REJECTED_DIGEST_MISMATCH")):
        out = api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT",
                                                      "scenario": scenario}, expected=200).json()
        assert {r["validation_status"] for r in out["receipts"]} == {status}


def test_out_of_order_receipt_preserves_history(api):
    plan, pkg, bundle = exported_and_piloted(api)
    t = _assignment(api, bundle)
    ids = [{"native_identity": t["native_identity"], "config_digest": t["expected_digest"]}]
    newer = api.post("eli", "/receipts", _receipt(bundle, SUB, ids=ids, completed="2026-10-01T12:25:00Z", key="n"),
                     expected=200).json()
    older = api.post("eli", "/receipts", _receipt(bundle, SUB, result="FAILED", completed="2026-10-01T12:05:00Z",
                                                  key="o"), expected=200).json()
    assert newer["validation_status"] == "VALID" and older["validation_status"] == "OUT_OF_ORDER"
    assert _assignment(api, bundle)["state"] == "APPLIED"
    receipts = api.get("eli", f"/handoff-bundles/{bundle['id']}", expected=200).json()["receipts"]
    assert {r["receipt_key"] for r in receipts} >= {"n", "o"}


def test_drift_creates_follow_up_without_mutation(api, clock):
    plan, pkg, bundle = exported_and_piloted(api)
    api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "SUCCESS"},
             expected=200)
    t = _assignment(api, bundle)
    api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "MATCH"}, expected=201)
    assert _assignment(api, bundle)["state"] == "VERIFIED"
    drift = api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "DRIFT"},
                     expected=201).json()
    assert drift["observation"]["comparison"] == "MISMATCH"
    assert any("enforcementMode" in d for d in drift["observation"]["differences"])
    assert drift["delivery_target"]["state"] == "DRIFTED"
    work = api.get("eli", "/work-items?kind=DRIFT", expected=200).json()["items"]
    assert work and "No automatic correction" in work[0]["detail"]
    # An older observation does not replace newer state.
    old_time = (clock.now() - timedelta(hours=1)).isoformat()
    late = api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "MATCH",
                                                  "observed_at": old_time}, expected=201).json()
    assert late["observation"]["applied_to_state"] is False
    assert late["delivery_target"]["state"] == "DRIFTED"
    # Reconciliation records follow-up only; it never changes the delivery state back.
    summary = api.post("ada", "/admin/reconcile", expected=200).json()["summary"]
    assert _assignment(api, bundle)["state"] == "DRIFTED"
    assert isinstance(summary["drift_items"], list)


def test_stale_observation_cannot_verify(api, clock):
    plan, pkg, bundle = exported_and_piloted(api)
    api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "SUCCESS"},
             expected=200)
    t = _assignment(api, bundle)
    clock.advance(hours=30)
    old = (clock.now() - timedelta(hours=26)).isoformat()
    res = api.post("eli", "/mock-observations", {"delivery_target_id": t["id"], "scenario": "MATCH",
                                                 "observed_at": old}, expected=201).json()
    assert res["delivery_target"]["state"] == "APPLIED"
    assert any("stale" in n for n in res["observation"]["notes"])


def test_material_change_invalidates_approval(api):
    plan, pkg = approved_package(api)
    body = api.get("cara", f"/rollout-plans/{plan['id']}", expected=200).json()
    update = {k: body[k] for k in ("title", "target_scope_id", "pause_criteria", "rollback_plan",
                                   "prerequisite_changes", "deployment_instructions")}
    update["implementation_revision_id"] = body["implementation_revision_ids"][0]
    update["rings"] = body["rings"]
    update["pause_criteria"] = body["pause_criteria"] + ["Any new pause criterion"]
    api.put("cara", f"/rollout-plans/{plan['id']}", {**update, "expected_lock_version": body["lock_version"]},
            expected=200)
    gates = api.get("eli", f"/change-packages/{pkg['id']}/gates", expected=200).json()
    assert "manifest_current" in {g["id"] for g in gates["failing"]}
    r = api.post("eli", f"/change-packages/{pkg['id']}/export", expected=409).json()
    assert r["error"]["code"] == "GATE_FAILED"
    stale = api.get("eli", f"/change-packages/{pkg['id']}", expected=200).json()
    assert stale["status"] == "STALE"
    assert {d["decision"] for d in stale["decisions"]} == {"APPROVE"}  # history preserved
    new = create_package(api, plan["id"])
    assert new["status"] == "IN_REVIEW" and new["manifest_digest"] != pkg["manifest_digest"]
    approve(api, new, "sam", "SECURITY_APPROVER")
    assert approve(api, new, "eli", "CLOUD_ENGINEER").json()["status"] == "APPROVED"


def test_package_goes_stale_without_byte_changes(api, clock):
    """Approval validity, exception windows and evidence freshness are rechecked at export."""
    plan, pkg = approved_package(api)
    clock.advance(days=4)  # assessment older than 72h freshness threshold
    r = api.post("eli", f"/change-packages/{pkg['id']}/export", expected=409).json()
    failing = {d["id"] for d in r["error"]["details"]}
    assert "current_assessment" in failing
    assert api.get("eli", f"/change-packages/{pkg['id']}", expected=200).json()["status"] == "STALE"


def test_expiry_is_derived_on_read_and_reconciled_without_cloud_changes(api, clock):
    exc = api.get("cara", f"/exceptions/{RETAIL_PARTNER_EXC}", expected=200).json()
    assert exc["effective_status"] == "APPROVED"
    clock.advance(days=80)  # past the 75-day expiry, no reconcile has run
    exc = api.get("cara", f"/exceptions/{RETAIL_PARTNER_EXC}", expected=200).json()
    assert exc["governance_status"] == "APPROVED" and exc["effective_status"] == "EXPIRED"
    assert exc["disposition"] == "EXPIRED"
    summary = api.post("ada", "/admin/reconcile", expected=200).json()["summary"]
    assert RETAIL_PARTNER_EXC in summary["expired_exceptions"]
    assert "exc-aws-payments-migration-role" in summary["removal_handoffs"]  # no native expiry for SCPs
    assert "exc-az-sandbox-poc" in summary["cleanup_items"]  # Azure stopped honoring; object remains
    again = api.post("ada", "/admin/reconcile", expected=200).json()["summary"]
    assert again["work_references_created"] == [] and again["expired_exceptions"] == []
    aws = api.get("cara", "/exceptions/exc-aws-payments-migration-role", expected=200).json()
    assert aws["native_status"] == "REMOVAL_PENDING"  # not claimed removed
    assert aws["native_cleanup_pending"] is True


def test_renewal_creates_new_revision_and_keeps_old_expiry(api, clock):
    old = api.get("riley", f"/exceptions/{RETAIL_PARTNER_EXC}", expected=200).json()
    renewal = api.post("riley", f"/exceptions/{RETAIL_PARTNER_EXC}/renew", {
        "expires_at": "2026-12-30T00:00:00Z",
        "business_justification": "Partner peering slipped by one month; renewal requested.",
        "technical_justification": "Same constraint as before; peering ticket NET-77.",
        "compensating_controls": ["Partner IP allow-list"]}, expected=201).json()
    assert renewal["renews_exception_id"] == RETAIL_PARTNER_EXC
    assert renewal["revision"] == 2 and renewal["lineage_id"] == old["lineage_id"]
    assert renewal["governance_status"] == "REQUESTED"
    still = api.get("riley", f"/exceptions/{RETAIL_PARTNER_EXC}", expected=200).json()
    assert still["expires_at"] == old["expires_at"] and still["governance_status"] == "APPROVED"
    lineage = api.get("riley", f"/exceptions/{renewal['id']}", expected=200).json()["lineage"]
    assert [e["revision"] for e in lineage] == [1, 2]
    # Requesters cannot approve their own requests.
    api.post("sam", f"/exceptions/{renewal['id']}/start-review", {"expected_lock_version": 1}, expected=200)
    api.post("riley", f"/exceptions/{renewal['id']}/decision", {
        "decision": "APPROVE", "rationale": "self approval attempt", "expected_lock_version": 2}, expected=403)


def test_rollout_pause_and_cancel(api):
    plan, pkg, bundle = exported_and_piloted(api)
    lv = api.get("eli", f"/rollout-plans/{plan['id']}", expected=200).json()["lock_version"]
    paused = api.post("sam", f"/rollout-plans/{plan['id']}/pause", {"reason": "Unexpected denies reported",
                                                                     "expected_lock_version": lv}, expected=200).json()
    assert paused["state"] == "PAUSED"
    advance(api, plan["id"], "LIMITED", expected=409)
    out = api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "SUCCESS"},
                   expected=200).json()
    assert {r["validation_status"] for r in out["receipts"]} == {"VALID"}  # paused != cancelled
    cancelled = api.post("eli", f"/rollout-plans/{plan['id']}/cancel", {
        "reason": "Superseded by a new design", "expected_lock_version": paused["lock_version"]}, expected=200).json()
    assert cancelled["state"] == "CANCELLED"
    out = api.post("eli", "/mock-pipeline/runs", {"bundle_id": bundle["id"], "ring": "PILOT", "scenario": "SUCCESS"},
                   expected=200).json()
    assert {r["validation_status"] for r in out["receipts"]} == {"REJECTED_NOT_AUTHORIZED"}
