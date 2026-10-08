"""Impact Assessment semantics."""

from __future__ import annotations

from journey import AZ_IMPL_REV, assess, submit_control_and_impl

SEARCH = "/subscriptions/00000000-0000-0000-0000-000000000"


def _results(api, run_id, **filters):
    q = "&".join(f"{k}={v}" for k, v in filters.items())
    return api.get("cara", f"/assessments/{run_id}/results?limit=200&{q}", expected=200).json()["items"]


def test_counts_reconcile_and_missing_data_stays_unknown(api):
    run = assess(api)
    cfg = run["rollup"]["configuration"]
    assert cfg["counts"] == {"COMPLIANT": 1, "NON_COMPLIANT": 5, "UNKNOWN": 1, "NOT_APPLICABLE": 1}
    assert sum(cfg["counts"].values()) == cfg["total_resources_evaluated"] == 8
    assert cfg["reconciles"] is True
    unknown = _results(api, run["id"], configuration_result="UNKNOWN")
    assert [r["subject_name"] for r in unknown] == ["srch-analytics-unknown"]
    assert "properties.publicNetworkAccess" in unknown[0]["missing_fields"]
    assert any("does not infer" in reason for reason in unknown[0]["reasons"])
    na = _results(api, run["id"], configuration_result="NOT_APPLICABLE")
    assert [r["resource_type"] for r in na] == ["Microsoft.Storage/storageAccounts"]
    # Lower-case 'disabled' is compliant (case-insensitive comparison, as in Azure Policy).
    ok = _results(api, run["id"], configuration_result="COMPLIANT")
    assert ok[0]["subject_name"] == "srch-retail-catalog"


def test_exception_dispositions_are_distinct_and_do_not_change_compliance(api):
    run = assess(api)
    rows = {r["subject_name"]: r for r in _results(api, run["id"], subject_kind="RESOURCE")}
    assert rows["srch-analytics-explore"]["exception_disposition"] == "PENDING"
    assert rows["srch-retail-partner"]["exception_disposition"] == "APPROVED_UNAPPLIED"
    assert rows["srch-payments-legacy"]["exception_disposition"] == "EFFECTIVE"
    assert rows["srch-sandbox-poc"]["exception_disposition"] == "EXPIRED"
    for name in ("srch-analytics-explore", "srch-retail-partner", "srch-payments-legacy", "srch-sandbox-poc"):
        assert rows[name]["configuration_result"] == "NON_COMPLIANT"  # exceptions never become compliance
    counts = run["rollup"]["exceptions"]["counts"]
    assert counts["PENDING"] == counts["APPROVED_UNAPPLIED"] == counts["EFFECTIVE"] == counts["EXPIRED"] == 1
    # Exemptions are per assignment: an exemption on the existing audit assignment does not exempt from the new one.
    assert rows["srch-payments-legacy"]["readiness"] == "BLOCKED"


def test_request_impact_is_separate_from_resource_compliance(api):
    run = assess(api)
    req = run["rollup"]["request_impact"]
    assert req["evidence_present"] is True
    assert req["counts"] == {"PREDICTED_DENIED": 3, "NOT_DENIED_BY_THIS_CONTROL": 3, "UNKNOWN": 1}
    assert run["rollup"]["configuration"]["counts"]["NON_COMPLIANT"] == 5  # not equal to predicted denies
    rows = {r["subject_id"]: r for r in _results(api, run["id"], subject_kind="REQUEST")}
    assert rows["REQ-AZ-3"]["request_impact"] == "UNKNOWN"  # PATCH merge semantics not modelled
    assert rows["REQ-AZ-5"]["request_impact"] == "NOT_DENIED_BY_THIS_CONTROL"  # DELETE
    assert "not proof" in rows["REQ-AZ-1"]["reasons"][0]
    assert rows["REQ-AZ-4"]["request_impact"] == "PREDICTED_DENIED"  # approved exemption not yet applied

    no_req = assess(api, use_request_fixtures=False)
    r2 = no_req["rollup"]["request_impact"]
    assert r2["evidence_present"] is False
    assert r2["counts"] is None and r2["potentially_blocked_operations"] == "UNKNOWN"
    assert no_req["confidence"]["request_impact"]["category"] == "NONE"


def test_unsupported_template_change_cannot_pass(api):
    submit_control_and_impl(api)
    impl = api.get("cara", "/implementations/impl-az-search-pna", expected=200).json()
    rev = impl["revisions"][-1]
    doc = rev["native_document"]
    doc["properties"]["policyRule"]["if"]["allOf"][1]["notEquals"] = "Enabled"  # changed rule
    body = {k: rev[k] for k in ("policy_kind", "source_kind", "source_ref", "pinned_version", "parameters",
                                 "assignment_settings", "prerequisites", "limitations")}
    new = api.post("cara", "/implementations/impl-az-search-pna/revisions",
                   {**body, "native_document": doc, "change_reason": "tweak rule"}, expected=201).json()
    v = api.post("cara", f"/implementation-revisions/{new['id']}/validate", expected=200).json()
    assert v["outcome"] == "UNSUPPORTED" and v["evaluator_id"] is None
    run = api.post("cara", "/assessments", {"implementation_revision_id": new["id"],
                                            "target_scope_id": "az-mg-contoso"}, expected=201).json()
    assert run["status"] == "UNSUPPORTED"
    assert run["rollup"]["status"] == "UNSUPPORTED" and "configuration" not in run["rollup"]
    assert api.get("cara", f"/assessments/{run['id']}/results", expected=200).json()["total"] == 0
    api.post("cara", f"/implementation-revisions/{new['id']}/submit", {"expected_lock_version": 1}, expected=409)

    # The referenced draft is preserved, not deleted.
    api.call("cara", "DELETE", f"/implementation-revisions/{new['id']}", expected=409)
    # Version drift gets no evaluator either.
    drift = {**body, "pinned_version": "1.0.0", "native_document": rev["native_document"],
             "change_reason": "pin older version", "expected_lock_version": new["lock_version"]}
    r = api.put("cara", f"/implementation-revisions/{new['id']}", drift, expected=200).json()
    v2 = api.post("cara", f"/implementation-revisions/{r['id']}/validate", expected=200).json()
    assert v2["outcome"] == "UNSUPPORTED"
    assert any(c["id"] == "pinned_version" and c["status"] == "UNSUPPORTED" for c in v2["checks"])


def test_assessment_never_runs_for_unauthorised_roles(api):
    api.post("riley", "/assessments", {"implementation_revision_id": AZ_IMPL_REV,
                                       "target_scope_id": "az-mg-contoso"}, expected=403)
    api.post("ada", "/assessments", {"implementation_revision_id": AZ_IMPL_REV,
                                     "target_scope_id": "az-mg-contoso"}, expected=403)


def test_imported_control_has_no_false_pass(api):
    run = api.post("cara", "/assessments", {"implementation_revision_id": "irev-az-storage-pna-1",
                                            "target_scope_id": "az-mg-contoso"}, expected=201).json()
    assert run["status"] == "UNSUPPORTED"
    detail = api.get("cara", "/controls/CTL-AZ-STORAGE-PNA", expected=200).json()
    assert detail["control"]["origin"] == "EXTERNALLY_MANAGED"
    assert detail["control"]["operational_owner"] == "Cloud Engineering"
