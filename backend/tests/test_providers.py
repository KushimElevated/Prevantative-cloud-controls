"""Provider contract unit tests (no network, no cloud)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

from app.core.digests import canonical_json, digest
from app.models.entities import ImplementationRevision, ResourceSnapshot
from app.providers.aws_scp import AwsScpProvider, parse_protective_document, render_template
from app.providers.azure_policy import AzurePolicyProvider, format_expires_on
from app.providers.verification import AZURE_SEARCH_PNA, AZURE_SEARCH_PNA_DEFINITION, AZURE_VERIFICATION


def _az_rev(**over):
    doc = copy.deepcopy(AZURE_SEARCH_PNA_DEFINITION)
    base = dict(id="r", implementation_id="i", policy_kind="AZURE_POLICY_BUILTIN_ASSIGNMENT", source_kind="BUILT_IN",
                source_ref=AZURE_SEARCH_PNA["definition_id"], pinned_version="1.0.1", native_document=doc,
                content_digest=digest(doc), parameters={"effect": "Deny"},
                assignment_settings={"enforcementMode": "Default"}, verification=AZURE_VERIFICATION)
    base.update(over)
    return ImplementationRevision(**base)


def test_verified_builtin_identity():
    assert AZURE_SEARCH_PNA["definition_id"].endswith("ee980b6d-0eca-4501-8d54-f6290fd512c3")
    assert AZURE_SEARCH_PNA["version"] == "1.0.1"
    assert AZURE_SEARCH_PNA["allowed_effects"] == ["Audit", "Deny", "Disabled"]
    assert AZURE_SEARCH_PNA["alias"] == "Microsoft.Search/searchServices/publicNetworkAccess"


def test_azure_evaluator_selection_is_strict():
    p = AzurePolicyProvider()
    assert p.evaluator_for(_az_rev()) is not None
    changed = copy.deepcopy(AZURE_SEARCH_PNA_DEFINITION)
    changed["properties"]["policyRule"]["then"]["effect"] = "deny"
    assert p.evaluator_for(_az_rev(native_document=changed, content_digest=digest(changed))) is None
    assert p.evaluator_for(_az_rev(pinned_version="1.0.0")) is None
    assert p.evaluator_for(_az_rev(source_kind="CUSTOM")) is None
    assert p.evaluator_for(_az_rev(parameters={"effect": "Modify"})) is None
    assert p.evaluator_for(_az_rev(assignment_settings={"enforcementMode": "Enroll"})) is None


def test_azure_configuration_semantics():
    p = AzurePolicyProvider()
    assert p._evaluate_config("Microsoft.Search/searchServices",
                              {"properties": {"publicNetworkAccess": "DISABLED"}}, []).configuration_result == "COMPLIANT"
    assert p._evaluate_config("microsoft.search/searchservices",
                              {"properties": {"publicNetworkAccess": "Enabled"}}, []).configuration_result == "NON_COMPLIANT"
    assert p._evaluate_config("Microsoft.Search/searchServices", {"properties": {}}, []).configuration_result == "UNKNOWN"
    assert p._evaluate_config("Microsoft.Web/sites", {}, []).configuration_result == "NOT_APPLICABLE"


def test_azure_ring_settings_and_observation_compare():
    p = AzurePolicyProvider()
    rev = _az_rev()
    assert p.validate_ring_settings("OBSERVATION", {"effect": "Deny", "enforcementMode": "Default"}, rev)[0].status == "FAIL"
    assert any(c.status == "UNSUPPORTED" for c in p.validate_ring_settings("PILOT", {"effect": "Modify"}, rev))
    assert p.ring_is_enforcing({"effect": "Deny", "enforcementMode": "Default"})
    assert not p.ring_is_enforcing({"effect": "Deny", "enforcementMode": "DoNotEnforce"})
    expected = {"scope": "/subscriptions/x", "properties": {"policyDefinitionId": AZURE_SEARCH_PNA["definition_id"],
                                                            "parameters": {"effect": {"value": "Deny"}},
                                                            "enforcementMode": "Default", "notScopes": []}}
    assert p.compare_observed_state(expected, copy.deepcopy(expected), "AZURE_POLICY_ASSIGNMENT").result == "MATCH"
    drift = p.mock_observed_state(expected, "DRIFT", "AZURE_POLICY_ASSIGNMENT")
    assert p.compare_observed_state(expected, drift, "AZURE_POLICY_ASSIGNMENT").result == "MISMATCH"
    assert p.compare_observed_state(expected, None, "AZURE_POLICY_ASSIGNMENT").result == "MISSING"


def test_expires_on_format():
    assert format_expires_on(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "2026-12-31T23:59:00.0000000Z"


def test_aws_template_and_family_parsing():
    params = {"exempt_principal_arn_patterns": ["arn:aws:iam::*:role/Admin"]}
    doc = render_template(params)
    assert doc["Statement"][0]["Effect"] == "Deny"
    assert parse_protective_document(doc)["exempt_patterns"] == ["arn:aws:iam::*:role/Admin"]
    widened = copy.deepcopy(doc)
    widened["Statement"][0]["Action"].append("s3:PutBucketPolicy")
    assert parse_protective_document(widened) is None  # outside the supported family: not interpreted
    p = AwsScpProvider()
    rev = ImplementationRevision(id="a", implementation_id="i", policy_kind="AWS_SCP", source_kind="CUSTOM_TEMPLATE",
                                 source_ref="template:aws.scp.protect-s3-block-public-access", pinned_version="1.0.0",
                                 native_document=widened, content_digest=digest(widened), parameters=params,
                                 assignment_settings={}, verification={"status": "UNVERIFIED"})
    assert p.evaluator_for(rev) is None
    assert p.validate_ring_settings("OBSERVATION", {"effect": "Audit"}, rev)[0].status == "UNSUPPORTED"
    assert p.capabilities().native_expiry_supported is False
    assert "None" in p.capabilities().native_audit_mode


def test_aws_bucket_effective_protection():
    p = AwsScpProvider()
    from app.providers.base import AssessmentContext
    from app.services.scopes import ScopeTree
    from app.models.entities import Scope
    acct = Scope(id="a1", provider="aws", native_id="111111111111", scope_type="AWS_ACCOUNT", parent_id=None,
                 display_name="a", is_management_account=False, provenance="FIXTURE")
    tree = ScopeTree(scopes={"a1": acct}, children={"a1": []})
    ctx = AssessmentContext(tree=tree, target_scope_id="a1", target_scope_ids={"a1"}, bindings=[],
                            now=datetime(2026, 10, 1, tzinfo=UTC))
    true4 = {k: True for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")}
    res = [ResourceSnapshot(resource_id="acct", resource_type="AWS::S3::AccountPublicAccessBlock", scope_id="a1",
                            configuration={"PublicAccessBlockConfiguration": true4}, missing_fields=[]),
           ResourceSnapshot(resource_id="b-unconfigured", resource_type="AWS::S3::Bucket", scope_id="a1",
                            configuration={"bucketType": "general-purpose",
                                           "PublicAccessBlockConfigurationStatus": "NOT_CONFIGURED"},
                            missing_fields=[]),
           ResourceSnapshot(resource_id="b-missing", resource_type="AWS::S3::Bucket", scope_id="a1",
                            configuration={}, missing_fields=["bucketType"])]
    out = p.assess_inventory(None, res, ctx)
    assert out["b-unconfigured"].configuration_result == "COMPLIANT"  # account level protects it
    assert out["b-missing"].applicability == "UNKNOWN"


def test_canonical_json_rejects_floats_and_is_order_independent():
    assert canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == '{"a":[2,{"c":2,"d":1}],"b":1}'
    assert digest({"x": 1, "y": 2}) == digest({"y": 2, "x": 1})
    with pytest.raises(ValueError):
        canonical_json({"x": 1.5})
