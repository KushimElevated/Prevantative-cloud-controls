"""Provider documentation verification records.

These records are the only place native identifiers, aliases and actions enter the system.
Anything not recorded here as verified from a primary source blocks integration-ready status.
See docs/control-feasibility.md for the full narrative.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.digests import digest
from app.models.enums import VerificationStatus

_REFS = Path(__file__).parent / "builtin_refs"

VERIFICATION_DATE = "2026-10-08"

AZURE_SEARCH_PNA_FILE = "azure-policy-ee980b6d-0eca-4501-8d54-f6290fd512c3-v1.0.1.json"
AZURE_SEARCH_PNA_DEFINITION: dict = json.loads((_REFS / AZURE_SEARCH_PNA_FILE).read_text())
AZURE_SEARCH_PNA_DIGEST = digest(AZURE_SEARCH_PNA_DEFINITION)

AZURE_SEARCH_PNA = {
    "definition_id": AZURE_SEARCH_PNA_DEFINITION["id"],
    "definition_name": AZURE_SEARCH_PNA_DEFINITION["name"],
    "display_name": AZURE_SEARCH_PNA_DEFINITION["properties"]["displayName"],
    "version": AZURE_SEARCH_PNA_DEFINITION["properties"]["version"],
    "mode": AZURE_SEARCH_PNA_DEFINITION["properties"]["mode"],
    "alias": "Microsoft.Search/searchServices/publicNetworkAccess",
    "allowed_effects": AZURE_SEARCH_PNA_DEFINITION["properties"]["parameters"]["effect"]["allowedValues"],
    "default_effect": AZURE_SEARCH_PNA_DEFINITION["properties"]["parameters"]["effect"]["defaultValue"],
    "content_digest": AZURE_SEARCH_PNA_DIGEST,
}

AZURE_VERIFICATION = {
    "status": VerificationStatus.VERIFIED_PRIMARY_SOURCE.value,
    "verified_on": VERIFICATION_DATE,
    "method": "Fetched primary-source files from Microsoft-owned GitHub repositories (Azure/azure-policy built-in "
    "definition JSON; MicrosoftDocs/azure-docs and MicrosoftDocs/azure-ai-docs source markdown). "
    "learn.microsoft.com itself was blocked by the build environment's network policy.",
    "sources": [
        {
            "url": "https://raw.githubusercontent.com/Azure/azure-policy/master/built-in-policies/policyDefinitions/"
            "Search/RequirePublicNetworkAccessDisabled_Deny.json",
            "sha256": "cfac363cf022ec4bfa2a81aa6ab375a1d1e9b13d6d35e6159d7b1581bc2eb104",
            "establishes": "Definition id ee980b6d-0eca-4501-8d54-f6290fd512c3, version 1.0.1, mode Indexed, "
            "effects Audit/Deny/Disabled (default Audit), rule: type == Microsoft.Search/searchServices AND "
            "Microsoft.Search/searchServices/publicNetworkAccess notEquals 'Disabled'.",
        },
        {
            "url": "https://learn.microsoft.com/en-us/azure/governance/policy/concepts/effect-deny "
            "(source: MicrosoftDocs/azure-docs articles/governance/policy/concepts/effect-deny.md, ms.date 03/04/2025)",
            "sha256": "3c329caca00469ddc71a1dde92e6dfa59a830eae88578ae34b15c57bc34ba7ed",
            "establishes": "Deny prevents matching create/update requests in Resource Manager modes (403); "
            "existing matching resources are marked non-compliant, not changed.",
        },
        {
            "url": "https://learn.microsoft.com/en-us/azure/governance/policy/concepts/exemption-structure "
            "(source markdown, ms.date 07/30/2026)",
            "sha256": "52a1768a0fc42f22f7ef06b264588c479fa7db949312b55437c1eca7884be821",
            "establishes": "Exemptions reference one policyAssignmentId; policyDefinitionReferenceId for initiative "
            "members; categories Waiver/Mitigated; expiresOn optional; expired exemptions are not deleted but are no "
            "longer honored; scope must be at or under the assignment unless assignmentScopeValidation=DoNotValidate "
            "(preview, not used here).",
        },
        {
            "url": "https://learn.microsoft.com/en-us/azure/governance/policy/concepts/assignment-structure "
            "(source markdown, ms.date 07/30/2026)",
            "sha256": "d323df3e367f90f7b6a1548d591a3f408d194e614133039d2c91e94cb07858f6",
            "establishes": "enforcementMode Default | DoNotEnforce | Enroll is separate from the definition effect; "
            "notScopes exclusions differ from exemptions.",
        },
        {
            "url": "https://learn.microsoft.com/en-us/azure/governance/policy/concepts/definition-structure-policy-rule "
            "(source markdown, ms.date 03/19/2025)",
            "sha256": "dce260d4447539cf27103958ae834a39df390081a1b5084e1bc02a5b9174ef4c",
            "establishes": "Conditions other than match/notMatch compare string values case-insensitively.",
        },
    ],
    "not_verified": [
        "Whether assignments can pin a built-in definition version (definitionVersion); artifacts do not emit it.",
        "Default value of publicNetworkAccess when the property is absent; the evaluator never infers it.",
        "Policy Insights evaluation timing after assignment; observations in this MVP are fixtures.",
        "Identity-based exemption selectors (userPrincipalId/groupPrincipalId); not modelled.",
    ],
}

AWS_VERIFICATION = {
    "status": VerificationStatus.UNVERIFIED.value,
    "verified_on": VERIFICATION_DATE,
    "method": "Primary AWS documentation (docs.aws.amazon.com) was blocked by the build environment's network "
    "policy. Facts below were corroborated only through web-search excerpts of official AWS pages. That is not "
    "primary-source verification, so the SCP implementation is DEMO-ONLY and integration-ready status is blocked.",
    "sources": [
        {
            "url": "https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps.html",
            "establishes": "(search excerpt) SCPs do not affect users/roles in the management account or "
            "service-linked roles; SCPs restrict member-account principals including the root user; SCPs do not "
            "grant permissions.",
        },
        {
            "url": "https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps_evaluation.html",
            "establishes": "(search excerpt) An explicit Deny at any level applies to all OUs/accounts beneath it; "
            "an Allow is needed at every level; a lower-level Allow cannot override an inherited Deny.",
        },
        {
            "url": "https://docs.aws.amazon.com/AmazonS3/latest/API/API_DeletePublicAccessBlock.html and "
            "API_control_PutPublicAccessBlock.html",
            "establishes": "(search excerpt) Bucket-level Put/DeletePublicAccessBlock require "
            "s3:PutBucketPublicAccessBlock; account-level Put/Delete require s3:PutAccountPublicAccessBlock.",
        },
        {
            "url": "https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_scps_examples_general.html",
            "establishes": "(search excerpt) Deny statements may exempt principals with ArnNotLike on "
            "aws:PrincipalArn.",
        },
        {
            "url": "https://docs.aws.amazon.com/organizations/latest/userguide/orgs_manage_policies_s3.html",
            "establishes": "(search excerpt) Organizations S3 policies can centrally manage S3 Block Public Access "
            "settings for member accounts. Recorded as a native alternative only.",
        },
    ],
    "not_verified": [
        "Exact SCP size quota and statement limits.",
        "Whether any S3 condition key exposes requested Block Public Access values (none is assumed; the SCP "
        "does not inspect request payloads).",
        "Directory bucket Block Public Access semantics (directory buckets are excluded from evaluation).",
        "Interaction between Organizations S3 policies and SCP-protected account settings.",
    ],
}

UNVERIFIED_REFERENCE = {
    "status": VerificationStatus.UNVERIFIED.value,
    "verified_on": VERIFICATION_DATE,
    "method": "Reference recorded from an existing deployment inventory without fetching the definition body.",
    "sources": [],
    "not_verified": ["Definition rule body, version and parameters were not retrieved."],
}
