"""Deterministic seed.

Seeded identifiers and content are fixed; timestamps are relative to an anchor (default: the
clock's current hour) so freshness windows behave sensibly whenever the demo is run. With the
same anchor the seed is byte-for-byte reproducible.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import load_principal
from app.core.clock import FixedClock, iso
from app.core.config import Settings
from app.core.digests import digest
from app.models import entities as m
from app.providers.aws_scp import TEMPLATE_REF, TEMPLATE_VERSION, render_template
from app.providers.azure_policy import format_expires_on
from app.providers.verification import AZURE_SEARCH_PNA, AZURE_SEARCH_PNA_DEFINITION
from app.services import audit, settings
from app.services.context import RequestContext
from app.services.controls import revision_content
from app.services.exceptions import content_digest
from app.services.implementations import submit_revision, validate_revision, verification_for

FIXTURES = Path(__file__).parent / "fixtures"
_REL = re.compile(r"^@T0(?:([+-])(\d+)([dhm]))?$")

USERS = [
    ("u-cara", "cara", "Cara Control-Engineer", "cara@contoso.example", "Cloud Security Engineering",
     [("CONTROL_ENGINEER", None), ("VIEWER", None)]),
    ("u-sam", "sam", "Sam Security-Approver", "sam@contoso.example", "Cloud Security",
     [("SECURITY_APPROVER", None), ("VIEWER", None)]),
    ("u-eli", "eli", "Eli Cloud-Engineer", "eli@contoso.example", "Cloud Engineering",
     [("CLOUD_ENGINEER", None), ("VIEWER", None)]),
    ("u-max", "max", "Max Multi-Role", "max@contoso.example", "Cloud Security",
     [("SECURITY_APPROVER", None), ("CLOUD_ENGINEER", None)]),
    ("u-ada", "ada", "Ada Admin", "ada@contoso.example", "Platform Administration", [("ADMIN", None)]),
    ("u-vic", "vic", "Vic Viewer", "vic@contoso.example", "Internal Audit", [("VIEWER", None)]),
    ("u-riley", "riley", "Riley Retail-Owner", "riley@contoso.example", "Retail Digital",
     [("EXCEPTION_REQUESTER", "az-sub-retail-prod"), ("VIEWER", "az-sub-retail-prod"),
      ("EXCEPTION_REQUESTER", "aws-acct-retail"), ("VIEWER", "aws-acct-retail")]),
    ("u-pat", "pat", "Pat Payments-Owner", "pat@contoso.example", "Payments",
     [("EXCEPTION_REQUESTER", "az-sub-payments-prod"), ("VIEWER", "az-sub-payments-prod"),
      ("EXCEPTION_REQUESTER", "aws-acct-payments"), ("VIEWER", "aws-acct-payments")]),
    ("u-dana", "dana", "Dana Analytics-Owner", "dana@contoso.example", "Analytics",
     [("EXCEPTION_REQUESTER", "az-sub-analytics-dev"), ("VIEWER", "az-sub-analytics-dev")]),
    ("u-lee", "lee", "Lee Lab-Owner", "lee@contoso.example", "Innovation Lab",
     [("EXCEPTION_REQUESTER", "az-sub-sandbox"), ("VIEWER", "az-sub-sandbox"),
      ("EXCEPTION_REQUESTER", "aws-acct-sandbox"), ("VIEWER", "aws-acct-sandbox")]),
]


def resolve(value: Any, anchor: datetime) -> Any:
    """Replace '@T0±N(d|h|m)' strings with absolute timestamps."""
    if isinstance(value, str):
        match = _REL.match(value)
        if match:
            sign, num, unit = match.groups()
            if not sign:
                return anchor
            delta = {"d": timedelta(days=int(num)), "h": timedelta(hours=int(num)),
                     "m": timedelta(minutes=int(num))}[unit]
            return anchor + delta if sign == "+" else anchor - delta
        return value
    if isinstance(value, dict):
        return {k: resolve(v, anchor) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, anchor) for v in value]
    return value


def default_anchor(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _ctx(session: Session, user_id: str, anchor: datetime, app_settings: Settings) -> RequestContext:
    return RequestContext(session=session, principal=load_principal(session, user_id), clock=FixedClock(anchor),
                          settings=app_settings)


def _azure_assignment(scope_native: str, name: str, effect: str, mode: str = "Default") -> dict:
    return {"name": name, "scope": scope_native, "properties": {
        "displayName": "Azure AI Search services should disable public network access (Cloud Engineering baseline)",
        "policyDefinitionId": AZURE_SEARCH_PNA["definition_id"], "parameters": {"effect": {"value": effect}},
        "enforcementMode": mode, "notScopes": []}}


def _scp_state(name: str, target: str, content: dict) -> dict:
    return {"PolicyName": name, "PolicyType": "SERVICE_CONTROL_POLICY", "TargetId": target,
            "PolicyContentDigest": digest(content), "Content": content}


def seed(session: Session, anchor: datetime, app_settings: Settings) -> dict[str, Any]:
    if session.scalar(select(func.count()).select_from(m.User)):
        raise RuntimeError("Database already contains data. Use `reset` for a clean, repeatable seed.")
    T = lambda s: resolve(s, anchor)  # noqa: E731
    settings.ensure_defaults(session, anchor)

    for sc in _load("scopes.json")["scopes"]:
        session.add(m.Scope(id=sc["id"], provider=sc["provider"], native_id=sc["native_id"],
                            scope_type=sc["scope_type"], parent_id=sc["parent_id"], display_name=sc["display_name"],
                            environment=sc["environment"], business_owner=sc["business_owner"],
                            application=sc["application"], is_management_account=sc.get("is_management_account", False),
                            provenance="FIXTURE", created_at=anchor))
        session.flush()
    for uid, username, name, email, team, grants in USERS:
        session.add(m.User(id=uid, username=username, display_name=name, email=email, team=team, is_active=True,
                           created_at=anchor))
        session.flush()
        for i, (role, scope) in enumerate(grants):
            session.add(m.RoleAssignment(id=f"ra-{username}-{i}", user_id=uid, role=role, scope_id=scope,
                                         created_at=anchor, created_by=None))
    session.flush()

    for fname in ("azure_inventory.json", "aws_inventory.json"):
        data = resolve(_load(fname), anchor)
        snap = data["snapshot"]
        content = [{k: v for k, v in r.items()} for r in data["resources"]]
        session.add(m.InventorySnapshot(id=snap["id"], provider=snap["provider"], label=snap["label"],
                                        source=snap["source"], provenance="FIXTURE", collected_at=snap["collected_at"],
                                        content_digest=digest(content), created_at=anchor))
        session.flush()
        for i, r in enumerate(data["resources"]):
            session.add(m.ResourceSnapshot(
                id=f"{snap['id']}-r{i:02d}", snapshot_id=snap["id"], resource_id=r["resource_id"], name=r["name"],
                provider=snap["provider"], resource_type=r["resource_type"], scope_id=r["scope_id"],
                configuration=r["configuration"], application=r["application"], owner=r["owner"],
                criticality=r["criticality"], collected_at=snap["collected_at"], source=snap["source"],
                missing_fields=r["missing_fields"]))
        rf = data["request_fixtures"]
        session.add(m.RequestFixtureSet(id=rf["id"], provider=snap["provider"], label=rf["label"], provenance="FIXTURE",
                                        items=rf["items"], content_digest=digest(rf["items"]), created_at=anchor))
    session.flush()

    _seed_azure(session, anchor, T, app_settings)
    _seed_aws(session, anchor, T, app_settings)
    _seed_imported(session, anchor, app_settings)
    audit.record_raw(session, principal=None, now=anchor, action="seed.loaded", object_type="system",
                     object_id="seed", correlation_id="seed",
                     details={"anchor": iso(anchor), "provenance": "FIXTURE"})
    session.flush()
    return {"anchor": iso(anchor), "users": len(USERS),
            "controls": session.scalar(select(func.count()).select_from(m.Control))}


# ------------------------------------------------------------------------------------ Azure


def _seed_azure(session: Session, anchor: datetime, T, app_settings: Settings) -> None:
    cid = "CTL-AZ-SEARCH-PNA"
    session.add(m.Control(id=cid, origin="PROPOSED", operational_owner="Cloud Engineering (enforcement)",
                          created_at=T("@T0-14d"), created_by="u-cara"))
    session.flush()
    rev = m.ControlRevision(
        id="crev-az-search-pna-1", control_id=cid, revision=1, status="DRAFT",
        name="Azure AI Search public network access disabled",
        description="Enterprise Azure AI Search services must not accept data-plane traffic from public networks.",
        security_objective="Enterprise Azure AI Search services have public network access disabled, with governed "
                           "time-bound exceptions where technically supported.",
        rationale="Recurring findings show search services created with public endpoints during prototyping and "
                  "never closed. Search indexes frequently contain copies of sensitive business data.",
        source_evidence=[
            {"kind": "WIZ_ISSUE", "reference": "fixture://wiz/issues/search-public-endpoint (illustrative)",
             "summary": "Recurring posture finding: Azure AI Search services with public network access enabled."},
            {"kind": "INCIDENT", "reference": "fixture://incidents/IR-2026-031 (illustrative)",
             "summary": "Prototype index containing customer support transcripts reachable from the internet."},
        ],
        severity="HIGH", providers=["azure"], resource_types=["Microsoft.Search/searchServices"],
        applicability_criteria="All Microsoft.Search/searchServices in enterprise management groups, except scopes "
                               "explicitly excluded by Cloud Engineering (none today).",
        security_owner="Cloud Security - Data Platform Controls", engineering_owner="Cloud Engineering - Policy Platform",
        framework_refs=["CIS Azure (network exposure)", "NIST SP 800-53 SC-7"], exception_eligible=True,
        prevention_boundary="Prevents create/update requests through Azure Resource Manager that would leave "
                            "publicNetworkAccess enabled on in-scope search services. Does not remediate existing "
                            "services, does not govern data-plane keys, and does not prove absence of data exposure.",
        limitations=[
            "Existing non-compliant services remain until remediated by their owners.",
            "Approved exceptions require a native exemption per assignment before enforcement.",
            "Resources excluded via notScopes or exempted are not prevented.",
            "Configuration evidence is not proof of network reachability or data exposure.",
        ],
        change_reason="Proposed from recurring Wiz findings and IR-2026-031.", created_by="u-cara",
        created_at=T("@T0-14d"), updated_at=T("@T0-14d"))
    session.add(rev)
    session.flush()
    impl = m.Implementation(id="impl-az-search-pna", control_id=cid, provider="azure",
                            name="Built-in assignment: AI Search public network access (Deny)",
                            mechanism_role="PRIMARY_GUARDRAIL", management="MANAGED_HERE",
                            created_at=T("@T0-13d"), created_by="u-cara")
    session.add(impl)
    session.flush()
    from app.providers.azure_policy import AzurePolicyProvider
    caps = AzurePolicyProvider().capabilities().to_dict()
    session.add(m.ImplementationRevision(
        id="irev-az-search-pna-1", implementation_id=impl.id, revision=1, control_revision_id=rev.id, status="DRAFT",
        policy_kind="AZURE_POLICY_BUILTIN_ASSIGNMENT", source_kind="BUILT_IN", source_ref=AZURE_SEARCH_PNA["definition_id"],
        pinned_version=AZURE_SEARCH_PNA["version"], content_digest=digest(AZURE_SEARCH_PNA_DEFINITION),
        parameters={"effect": "Deny"},
        assignment_settings={"enforcementMode": "Default", "notScopes": [],
                             "nonComplianceMessage": "Public network access must be disabled for Azure AI Search. "
                                                     "Request a time-bound exception through Cloud Security."},
        native_document=copy.deepcopy(AZURE_SEARCH_PNA_DEFINITION), capability_metadata=caps,
        prerequisites=["Private endpoint per client path", "privatelink.search.windows.net DNS resolution",
                       "Client connectivity validated on the private path", "Application owner sign-off"],
        limitations=["Assignment references the built-in by id; Microsoft may publish newer versions.",
                     "Deny does not remediate existing configurations."],
        verification=verification_for("azure", "AZURE_POLICY_BUILTIN_ASSIGNMENT", AZURE_SEARCH_PNA["definition_id"],
                                       AZURE_SEARCH_PNA["version"]),
        change_reason="Initial implementation reusing the built-in definition.", created_by="u-cara",
        created_at=T("@T0-13d"), updated_at=T("@T0-13d")))
    mg = "/providers/Microsoft.Management/managementGroups/contoso-root"
    existing = _azure_assignment(mg, "ce-search-pna-audit", "Audit")
    session.add(m.PolicyBinding(
        id="bind-az-existing-audit", control_id=cid, implementation_revision_id=None, provider="azure",
        binding_kind="AZURE_POLICY_ASSIGNMENT", native_id=f"{mg}/providers/Microsoft.Authorization/policyAssignments/"
                                                         "ce-search-pna-audit",
        definition_ref=AZURE_SEARCH_PNA["definition_id"], definition_digest=AZURE_SEARCH_PNA["content_digest"],
        target_scope_id="az-mg-contoso", settings={"effect": "Audit", "enforcementMode": "Default"}, exclusions=[],
        source_repository="fixture://cloud-eng/policy-baseline (externally managed)", management="EXTERNALLY_MANAGED",
        origin="EXISTING_IMPORTED", desired_state=existing, observed_state=copy.deepcopy(existing),
        observed_at=T("@T0-1h"), evidence_provenance="FIXTURE", created_at=T("@T0-30d")))
    session.flush()

    def exc(eid, requester, app, team, scope, resources, status, native, valid_from, expires, just, tech, comp,
            native_observed=None, observed_at=None):
        e = m.SecurityException(
            id=eid, lineage_id=eid, revision=1, renews_exception_id=None, control_id=cid, implementation_id=impl.id,
            binding_ids=[], requester_id=requester, application=app, team=team, business_justification=just,
            technical_justification=tech, scope_id=scope, granularity="RESOURCE", resource_ids=resources,
            principal_patterns=[], risk_owner=f"{team} business owner", compensating_controls=comp,
            valid_from=valid_from, expires_at=expires, governance_status=status, native_status=native,
            representability="REPRESENTABLE",
            representability_reason="Representable as a policy exemption with native expiry (expiresOn).",
            native_representation={"type": "Microsoft.Authorization/policyExemptions", "targets": resources,
                                   "exemptionCategory": "Waiver", "expiresOn": format_expires_on(expires)},
            native_expiry_supported=True, native_observed=native_observed, native_observed_at=observed_at,
            native_provenance="FIXTURE" if native_observed else None, work_ref=None,
            created_at=valid_from, updated_at=valid_from)
        session.add(e)
        session.flush()
        return e

    rg = "/subscriptions/00000000-0000-0000-0000-000000000"
    exc("exc-az-analytics-explore", "u-dana", "analytics-explorer", "Analytics", "az-rg-analytics",
        [f"{rg}201/resourceGroups/rg-analytics/providers/Microsoft.Search/searchServices/srch-analytics-explore"],
        "REQUESTED", "NOT_REQUESTED", T("@T0-1d"), T("@T0+60d"),
        "Exploratory analytics notebooks hosted outside the corporate network query this index during a "
        "time-boxed evaluation.",
        "Notebook service has no private networking option in the evaluation tier; migration planned.",
        ["IP firewall restricted to vendor egress ranges", "Query keys rotated weekly"])
    partner = exc("exc-az-retail-partner", "u-riley", "retail-partner-api", "Retail Digital", "az-rg-retail-search",
                  [f"{rg}101/resourceGroups/rg-retail-search/providers/Microsoft.Search/searchServices/srch-retail-partner"],
                  "APPROVED", "PENDING", T("@T0-2d"), T("@T0+75d"),
                  "External partner catalogue integration must keep working until partner VNet peering is live.",
                  "Partner calls the search endpoint from their own cloud; private link to the partner is in "
                  "progress (WORK-1234).",
                  ["Partner IP allow-list", "Read-only query key scoped to partner index", "Weekly access review"])
    legacy_assignment = f"{mg}/providers/Microsoft.Authorization/policyAssignments/ce-search-pna-audit"
    legacy_target = (f"{rg}102/resourceGroups/rg-payments-search/providers/Microsoft.Search/searchServices/"
                     "srch-payments-legacy")
    legacy_exp = T("@T0+40d")
    legacy = exc("exc-az-payments-legacy", "u-pat", "payments-legacy", "Payments", "az-rg-payments-search",
                 [legacy_target], "APPROVED", "APPLIED", T("@T0-50d"), legacy_exp,
                 "Legacy payments search is being decommissioned; the vendor integration cannot use private link.",
                 "Vendor SaaS connector has no private connectivity option.",
                 ["Vendor IP allow-list", "Index contains no cardholder data (validated)"],
                 native_observed={"name": "ce-exm-payments-legacy", "scope": legacy_target, "properties": {
                     "policyAssignmentId": legacy_assignment, "exemptionCategory": "Waiver",
                     "expiresOn": format_expires_on(legacy_exp)}},
                 observed_at=T("@T0-1h"))
    sandbox_target = f"{rg}202/resourceGroups/rg-sandbox/providers/Microsoft.Search/searchServices/srch-sandbox-poc"
    sandbox_exp = T("@T0-10d")
    sandbox = exc("exc-az-sandbox-poc", "u-lee", "sandbox-poc", "Innovation Lab", "az-rg-sandbox", [sandbox_target],
                  "APPROVED", "APPLIED", T("@T0-70d"), sandbox_exp,
                  "Hackathon proof of concept required public access for external judges.",
                  "Short-lived demo environment without private networking.",
                  ["Synthetic data only", "Service scheduled for deletion"],
                  native_observed={"name": "ce-exm-sandbox-poc", "scope": sandbox_target, "properties": {
                      "policyAssignmentId": legacy_assignment, "exemptionCategory": "Waiver",
                      "expiresOn": format_expires_on(sandbox_exp)}},
                  observed_at=T("@T0-2h"))
    for e, when in ((partner, "@T0-2d"), (legacy, "@T0-50d"), (sandbox, "@T0-70d")):
        session.add(m.ApprovalDecision(id=f"dec-seed-{e.id}", subject_type="EXCEPTION", subject_id=e.id,
                                       subject_digest=content_digest(e), actor_id="u-sam", role="SECURITY_APPROVER",
                                       decision="APPROVE", rationale="Time-bound waiver with compensating controls.",
                                       decided_at=T(when), correlation_id="seed"))

    def ev(eid, app, scope, prereq, status, summary, when):
        session.add(m.ReadinessEvidence(id=eid, control_id=cid, application=app, scope_id=scope, prerequisite=prereq,
                                        status=status, summary=summary, evidence_ref=f"fixture://evidence/{eid}",
                                        provided_by="Fixture", provenance="FIXTURE", collected_at=T(when),
                                        created_at=T(when), created_by=None))
    ev("ev-retail-pe", "retail-catalog", "az-sub-retail-prod", "PRIVATE_ENDPOINT", "SATISFIED",
       "Private endpoint pe-srch-retail-catalog approved and connected.", "@T0-5d")
    ev("ev-retail-dns", "retail-catalog", "az-sub-retail-prod", "DNS", "SATISFIED",
       "privatelink.search.windows.net resolves from app and build networks.", "@T0-5d")
    ev("ev-retail-owner", "retail-catalog", "az-sub-retail-prod", "APP_OWNER_VALIDATION", "SATISFIED",
       "Retail catalog owner confirms operation with public access disabled.", "@T0-3d")
    ev("ev-partner-pe", "retail-partner-api", "az-sub-retail-prod", "PRIVATE_ENDPOINT", "NOT_SATISFIED",
       "Partner integration still requires the public endpoint until VNet peering completes.", "@T0-4d")
    ev("ev-payments-kb-pe", "payments-kb", "az-sub-payments-prod", "PRIVATE_ENDPOINT", "NOT_SATISFIED",
       "Private endpoint not yet requested.", "@T0-6d")
    ev("ev-payments-kb-dns", "payments-kb", "az-sub-payments-prod", "DNS", "UNKNOWN",
       "DNS forwarding for on-prem clients not assessed.", "@T0-6d")
    ev("ev-analytics-dns", "analytics-explorer", "az-sub-analytics-dev", "DNS", "SATISFIED",
       "DNS validated during last quarter's network review.", "@T0-120d")
    session.flush()


# ------------------------------------------------------------------------------------ AWS


def _seed_aws(session: Session, anchor: datetime, T, app_settings: Settings) -> None:
    cid = "CTL-AWS-S3-PUBLIC-ACCESS"
    session.add(m.Control(id=cid, origin="PROPOSED", operational_owner="Cloud Engineering (enforcement)",
                          created_at=T("@T0-10d"), created_by="u-cara"))
    session.flush()
    rev = m.ControlRevision(
        id="crev-aws-s3-pa-1", control_id=cid, revision=1, status="DRAFT",
        name="Protect S3 public-access safeguards",
        description="Prevent unintended public access to in-scope S3 data and protect the required Block Public "
                    "Access safeguards from unauthorized changes.",
        security_objective="In-scope accounts keep account-level S3 Block Public Access fully enabled, and only "
                           "designated administrative roles can change Block Public Access settings.",
        rationale="Public-access misconfigurations recur when teams disable Block Public Access to unblock a "
                  "deployment. Protecting an established baseline is cheaper than repeated remediation.",
        source_evidence=[{"kind": "FINDING", "reference": "fixture://findings/s3-bpa-disabled (illustrative)",
                          "summary": "Account-level Block Public Access disabled in a production account."}],
        severity="HIGH", providers=["aws"], resource_types=["AWS::S3::AccountPublicAccessBlock", "AWS::S3::Bucket"],
        applicability_criteria="Member accounts under the Workloads OU. General purpose buckets only; directory "
                               "buckets excluded. The management account is not affected by SCPs.",
        security_owner="Cloud Security - Data Platform Controls", engineering_owner="Cloud Engineering - AWS Organizations",
        framework_refs=["CIS AWS Foundations (S3 Block Public Access)"], exception_eligible=True,
        prevention_boundary="An SCP denies non-exempt member-account principals from changing account- or "
                            "bucket-level Block Public Access. It protects a configured baseline; it does not "
                            "establish that baseline, does not prevent bucket creation or policy edits, and is not a "
                            "universal prohibition on public buckets.",
        limitations=[
            "Requires a verified account-level baseline before attachment (otherwise it locks in insecure settings).",
            "Blocks legitimate strengthening changes by non-exempt principals too.",
            "Does not apply to the management account or service-linked roles.",
            "Native alternative to evaluate with Cloud Engineering: AWS Organizations S3 policies for centrally "
            "managed Block Public Access (not assumed enabled; not implemented here).",
        ],
        change_reason="Proposed after repeated Block Public Access regressions.", created_by="u-cara",
        created_at=T("@T0-10d"), updated_at=T("@T0-10d"))
    session.add(rev)
    session.flush()
    ctx = _ctx(session, "u-cara", anchor, app_settings)
    rev.content_digest = digest(revision_content(rev))
    rev.status = "IN_REVIEW"
    rev.submitted_at = T("@T0-9d")
    rev.submitted_by = "u-cara"
    impl = m.Implementation(id="impl-aws-scp-bpa", control_id=cid, provider="aws",
                            name="SCP: protect S3 Block Public Access baseline", mechanism_role="BASELINE_PROTECTION",
                            management="MANAGED_HERE", created_at=T("@T0-9d"), created_by="u-cara")
    session.add(impl)
    session.flush()
    params = {"exempt_principal_arn_patterns": ["arn:aws:iam::*:role/PlatformS3BPAAdmin"]}
    doc = render_template(params)
    from app.providers.aws_scp import AwsScpProvider
    irev = m.ImplementationRevision(
        id="irev-aws-scp-bpa-1", implementation_id=impl.id, revision=1, control_revision_id=rev.id, status="DRAFT",
        policy_kind="AWS_SCP", source_kind="CUSTOM_TEMPLATE", source_ref=TEMPLATE_REF, pinned_version=TEMPLATE_VERSION,
        content_digest=digest(doc), parameters=params, assignment_settings={}, native_document=doc,
        capability_metadata=AwsScpProvider().capabilities().to_dict(),
        prerequisites=["Account-level Block Public Access fully enabled with provenance",
                       "PlatformS3BPAAdmin role and change procedure documented"],
        limitations=["DEMO-ONLY: AWS documentation not verified from a primary source in this build.",
                     "Denies strengthening changes too; administration must use the exempt role."],
        verification=verification_for("aws", "AWS_SCP", TEMPLATE_REF, TEMPLATE_VERSION),
        change_reason="Initial SCP template.", created_by="u-cara", created_at=T("@T0-9d"), updated_at=T("@T0-9d"))
    session.add(irev)
    session.flush()
    validate_revision(ctx, irev.id)
    submit_revision(ctx, irev.id, irev.lock_version)

    prod_content = {"Version": "2012-10-17", "Statement": [{
        "Sid": "ProdDenyAccountBPAChanges", "Effect": "Deny", "Action": ["s3:PutAccountPublicAccessBlock"],
        "Resource": "*", "Condition": {"ArnNotLike": {"aws:PrincipalArn": [
            "arn:aws:iam::*:role/PlatformS3BPAAdmin", "arn:aws:iam::222222222222:role/PaymentsMigration"]}}}]}
    state = _scp_state("ce-prod-deny-account-bpa", "ou-fx01-prod0001", prod_content)
    session.add(m.PolicyBinding(
        id="bind-aws-existing-prod-scp", control_id=cid, implementation_revision_id=None, provider="aws",
        binding_kind="AWS_SCP_ATTACHMENT", native_id="aws-organizations:policy/p-FIXTURE-0001/target/ou-fx01-prod0001",
        definition_ref="fixture://cloud-eng/scp/ce-prod-deny-account-bpa", definition_digest=digest(prod_content),
        target_scope_id="aws-ou-prod", settings={"attach": True}, exclusions=[],
        source_repository="fixture://cloud-eng/org-policies (externally managed)", management="EXTERNALLY_MANAGED",
        origin="EXISTING_IMPORTED", desired_state=state, observed_state=copy.deepcopy(state),
        observed_at=T("@T0-1h"), evidence_provenance="FIXTURE", created_at=T("@T0-200d")))
    session.flush()

    def exc(eid, requester, app, team, scope, gran, resources, principals, status, native, valid_from, expires,
            rep, reason, observed=None):
        e = m.SecurityException(
            id=eid, lineage_id=eid, revision=1, renews_exception_id=None, control_id=cid, implementation_id=impl.id,
            binding_ids=["bind-aws-existing-prod-scp"] if observed else [], requester_id=requester, application=app,
            team=team, business_justification="Business justification recorded by the application team for review.",
            technical_justification="Technical justification recorded by the application team for review.",
            scope_id=scope, granularity=gran, resource_ids=resources, principal_patterns=principals,
            risk_owner=f"{team} business owner", compensating_controls=["Documented in request"],
            valid_from=valid_from, expires_at=expires, governance_status=status, native_status=native,
            representability=rep, representability_reason=reason, native_representation=None,
            native_expiry_supported=False, native_observed=observed,
            native_observed_at=T("@T0-1h") if observed else None, native_provenance="FIXTURE" if observed else None,
            work_ref=None, created_at=valid_from, updated_at=valid_from)
        session.add(e)
        session.flush()
        return e

    exc("exc-aws-retail-public-bucket", "u-riley", "retail-web", "Retail Digital", "aws-acct-retail", "RESOURCE",
        ["arn:aws:s3:::retail-public-site-fixture"], [], "REQUESTED", "NOT_REQUESTED", T("@T0-1d"), T("@T0+30d"),
        "REQUIRES_DIFFERENT_MECHANISM",
        "An SCP cannot exempt an individual bucket, and a bucket-level exception cannot override account- or "
        "organization-level Block Public Access. Allowing public access for one bucket would require weakening the "
        "account baseline for every bucket in that account, which is a separate control decision.")
    exc("exc-aws-payments-account", "u-pat", "payments", "Payments", "aws-acct-payments", "SCOPE", [], [],
        "REQUESTED", "NOT_REQUESTED", T("@T0-1d"), T("@T0+20d"), "UNSUPPORTED",
        "An inherited explicit Deny from existing SCP aws-organizations:policy/p-FIXTURE-0001/target/ou-fx01-prod0001 "
        "attached at aws-ou-prod applies. A lower-scope allow or an exception in this control's SCP cannot undo it; "
        "the owner of that policy (Cloud Engineering) would need to change it.")
    migr = exc("exc-aws-payments-migration-role", "u-pat", "payments", "Payments", "aws-acct-payments", "PRINCIPAL", [],
               ["arn:aws:iam::222222222222:role/PaymentsMigration"], "APPROVED", "APPLIED", T("@T0-40d"), T("@T0-3d"),
               "REPRESENTABLE",
               "Representable as an ArnNotLike principal pattern. No native expiry: removal needs a Cloud "
               "Engineering handoff.", observed=state)
    session.add(m.ApprovalDecision(id=f"dec-seed-{migr.id}", subject_type="EXCEPTION", subject_id=migr.id,
                                   subject_digest=content_digest(migr), actor_id="u-sam", role="SECURITY_APPROVER",
                                   decision="APPROVE", rationale="Migration window approved.",
                                   decided_at=T("@T0-40d"), correlation_id="seed"))
    session.add(m.ReadinessEvidence(
        id="ev-aws-retail-admin", control_id=cid, application="*", scope_id="aws-acct-retail",
        prerequisite="BPA_ADMIN_PROCESS", status="SATISFIED",
        summary="PlatformS3BPAAdmin role exists; change and rollback runbook RB-S3-7 reviewed.",
        evidence_ref="fixture://evidence/ev-aws-retail-admin", provided_by="Fixture", provenance="FIXTURE",
        collected_at=T("@T0-2d"), created_at=T("@T0-2d"), created_by=None))
    session.flush()


# ------------------------------------------------------------------------------------ imported


def _seed_imported(session: Session, anchor: datetime, app_settings: Settings) -> None:
    """An existing control catalogued as externally managed. Cataloguing does not transfer ownership."""
    cid = "CTL-AZ-STORAGE-PNA"
    session.add(m.Control(id=cid, origin="EXTERNALLY_MANAGED", operational_owner="Cloud Engineering",
                          created_at=anchor - timedelta(days=5), created_by="u-cara"))
    session.flush()
    rev = m.ControlRevision(
        id="crev-az-storage-pna-1", control_id=cid, revision=1, status="DRAFT",
        name="Storage accounts disable public network access (existing baseline)",
        description="Catalogue entry for an existing Cloud Engineering baseline assignment.",
        security_objective="Storage accounts in enterprise scopes do not accept public network traffic.",
        rationale="Registered so coverage and duplicates are visible alongside new controls.",
        source_evidence=[{"kind": "AUDIT", "reference": "fixture://cloud-eng/policy-baseline",
                          "summary": "Existing assignment in the Cloud Engineering baseline repository."}],
        severity="MEDIUM", providers=["azure"], resource_types=["Microsoft.Storage/storageAccounts"],
        applicability_criteria="As defined by the existing assignment (not yet reviewed).",
        security_owner="Cloud Security", engineering_owner="Cloud Engineering",
        framework_refs=[], exception_eligible=False,
        prevention_boundary="Unknown until the definition body is retrieved and reviewed.",
        limitations=["Definition body not retrieved; no evaluator; assessments are UNSUPPORTED."],
        change_reason="Imported existing control for visibility.", created_by="u-cara",
        created_at=anchor - timedelta(days=5), updated_at=anchor - timedelta(days=5))
    session.add(rev)
    rev.content_digest = digest(revision_content(rev))
    rev.status = "IN_REVIEW"
    rev.submitted_at = anchor - timedelta(days=5)
    rev.submitted_by = "u-cara"
    session.flush()
    impl = m.Implementation(id="impl-az-storage-pna", control_id=cid, provider="azure",
                            name="Existing built-in assignment (reference only)", mechanism_role="DETECTIVE_REFERENCE",
                            management="EXTERNALLY_MANAGED", created_at=anchor - timedelta(days=5), created_by="u-cara")
    session.add(impl)
    session.flush()
    ref = "/providers/Microsoft.Authorization/policyDefinitions/b2982f36-99f2-4db5-8eff-283140c09693"
    from app.providers.azure_policy import AzurePolicyProvider
    irev = m.ImplementationRevision(
        id="irev-az-storage-pna-1", implementation_id=impl.id, revision=1, control_revision_id=rev.id, status="DRAFT",
        policy_kind="AZURE_POLICY_BUILTIN_ASSIGNMENT", source_kind="BUILT_IN", source_ref=ref, pinned_version=None,
        content_digest=None, parameters={"effect": "Audit"}, assignment_settings={"enforcementMode": "Default"},
        native_document=None, capability_metadata=AzurePolicyProvider().capabilities().to_dict(), prerequisites=[],
        limitations=["Reference only: definition id seen in Microsoft documentation; body not retrieved."],
        verification=verification_for("azure", "AZURE_POLICY_BUILTIN_ASSIGNMENT", ref, None),
        change_reason="Imported reference.", created_by="u-cara", created_at=anchor - timedelta(days=5),
        updated_at=anchor - timedelta(days=5))
    session.add(irev)
    session.flush()
    validate_revision(_ctx(session, "u-cara", anchor, app_settings), irev.id)
    mg = "/providers/Microsoft.Management/managementGroups/contoso-root"
    st = {"name": "ce-storage-pna-audit", "scope": mg, "properties": {
        "policyDefinitionId": ref, "parameters": {"effect": {"value": "Audit"}}, "enforcementMode": "Default",
        "notScopes": []}}
    session.add(m.PolicyBinding(
        id="bind-az-storage-audit", control_id=cid, implementation_revision_id=None, provider="azure",
        binding_kind="AZURE_POLICY_ASSIGNMENT",
        native_id=f"{mg}/providers/Microsoft.Authorization/policyAssignments/ce-storage-pna-audit",
        definition_ref=ref, definition_digest=None, target_scope_id="az-mg-contoso",
        settings={"effect": "Audit", "enforcementMode": "Default"}, exclusions=[],
        source_repository="fixture://cloud-eng/policy-baseline (externally managed)", management="EXTERNALLY_MANAGED",
        origin="EXISTING_IMPORTED", desired_state=st, observed_state=copy.deepcopy(st),
        observed_at=anchor - timedelta(hours=1), evidence_provenance="FIXTURE", created_at=anchor - timedelta(days=90)))
    session.flush()
