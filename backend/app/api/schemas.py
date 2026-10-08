"""Strict request models. Unknown fields are rejected; sizes are bounded."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Str = Field(min_length=1, max_length=4000)
ShortStr = Field(min_length=1, max_length=256)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvidenceItem(Strict):
    kind: Literal["INCIDENT", "FINDING", "AUDIT", "THREAT_INTEL", "WIZ_ISSUE", "OTHER"]
    reference: str = Field(min_length=1, max_length=512)
    summary: str = Field(min_length=1, max_length=2000)


class ControlContent(Strict):
    name: str = ShortStr
    description: str = Str
    security_objective: str = Str
    rationale: str = Str
    source_evidence: list[EvidenceItem] = Field(default_factory=list, max_length=50)
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    providers: list[Literal["azure", "aws"]] = Field(min_length=1, max_length=2)
    resource_types: list[str] = Field(min_length=1, max_length=20)
    applicability_criteria: str = Str
    security_owner: str = ShortStr
    engineering_owner: str = ShortStr
    framework_refs: list[str] = Field(default_factory=list, max_length=30)
    exception_eligible: bool
    prevention_boundary: str = Str
    limitations: list[str] = Field(default_factory=list, max_length=30)


class ControlCreate(ControlContent):
    id: str = Field(pattern=r"^CTL-[A-Z0-9-]{3,60}$")
    origin: Literal["PROPOSED", "IMPORTED", "EXTERNALLY_MANAGED"] = "PROPOSED"
    operational_owner: str = Field(default="Cloud Engineering", min_length=1, max_length=256)
    change_reason: str = Str


class ControlRevisionUpdate(ControlContent):
    change_reason: str = Str
    expected_lock_version: int


class NewRevision(Strict):
    change_reason: str = Str


class LockedCommand(Strict):
    expected_lock_version: int


class ReasonCommand(Strict):
    reason: str = Str


class ImplementationContent(Strict):
    policy_kind: Literal["AZURE_POLICY_BUILTIN_ASSIGNMENT", "AZURE_POLICY_CUSTOM_DEFINITION", "AWS_SCP"]
    source_kind: Literal["BUILT_IN", "CUSTOM_TEMPLATE", "CUSTOM", "REPOSITORY"]
    source_ref: str = Field(min_length=1, max_length=512)
    pinned_version: str | None = Field(default=None, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)
    assignment_settings: dict[str, Any] = Field(default_factory=dict)
    native_document: dict[str, Any] | None = None
    prerequisites: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class ImplementationCreate(ImplementationContent):
    provider: Literal["azure", "aws"]
    name: str = ShortStr
    mechanism_role: Literal["PRIMARY_GUARDRAIL", "BASELINE_PROTECTION", "DETECTIVE_REFERENCE"]
    management: Literal["MANAGED_HERE", "EXTERNALLY_MANAGED"] = "MANAGED_HERE"
    change_reason: str = Str


class ImplementationRevisionCreate(ImplementationContent):
    change_reason: str = Str


class ImplementationRevisionUpdate(ImplementationContent):
    change_reason: str = Str
    expected_lock_version: int


class AssessmentCreate(Strict):
    implementation_revision_id: str = ShortStr
    control_revision_id: str | None = None
    target_scope_id: str = ShortStr
    inventory_snapshot_id: str | None = None
    request_fixture_set_id: str | None = None
    use_request_fixtures: bool = True


class ReadinessEvidenceCreate(Strict):
    control_id: str = ShortStr
    application: str = ShortStr
    scope_id: str = ShortStr
    prerequisite: str = Field(min_length=1, max_length=64)
    status: Literal["SATISFIED", "NOT_SATISFIED", "UNKNOWN"]
    summary: str = Str
    evidence_ref: str | None = Field(default=None, max_length=512)
    collected_at: datetime | None = None


class WorkReferenceCreate(Strict):
    kind: Literal["REMEDIATION", "READINESS", "EXEMPTION_REMOVAL", "EXEMPTION_CLEANUP", "RENEWAL_REVIEW", "DRIFT",
                  "VERIFICATION_STALE", "DELIVERY_FAILURE"]
    owner_team: str = ShortStr
    title: str = Field(min_length=1, max_length=512)
    detail: str = Field(default="", max_length=4000)
    external_system: str | None = Field(default=None, max_length=64)
    external_ref: str | None = Field(default=None, max_length=256)
    related_type: str = ShortStr
    related_id: str = ShortStr
    scope_id: str | None = None


class ExceptionCreate(Strict):
    control_id: str = ShortStr
    implementation_id: str | None = None
    application: str = ShortStr
    team: str = ShortStr
    business_justification: str = Field(min_length=20, max_length=4000)
    technical_justification: str = Field(min_length=20, max_length=4000)
    scope_id: str = ShortStr
    granularity: Literal["RESOURCE", "SCOPE", "PRINCIPAL"]
    resource_ids: list[str] = Field(default_factory=list, max_length=50)
    principal_patterns: list[str] = Field(default_factory=list, max_length=10)
    risk_owner: str = ShortStr
    compensating_controls: list[str] = Field(min_length=1, max_length=20)
    valid_from: datetime | None = None
    expires_at: datetime
    work_ref: str | None = Field(default=None, max_length=256)

    @field_validator("expires_at", "valid_from")
    @classmethod
    def _aware(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("timestamps must include a UTC offset")
        return v


class ExceptionDecision(Strict):
    decision: Literal["APPROVE", "REJECT"]
    rationale: str = Field(min_length=10, max_length=4000)
    expected_lock_version: int


class ExceptionRenewal(Strict):
    expires_at: datetime
    business_justification: str = Field(min_length=20, max_length=4000)
    technical_justification: str = Field(min_length=20, max_length=4000)
    compensating_controls: list[str] = Field(min_length=1, max_length=20)


class RingSpec(Strict):
    stage: Literal["OBSERVATION", "PILOT", "LIMITED", "BROAD"]
    scope_ids: list[str] = Field(min_length=1, max_length=50)
    settings: dict[str, Any]


class RollbackPlan(Strict):
    prior_known_good: str = Str
    steps: list[str] = Field(min_length=1, max_length=30)
    limitations: list[str] = Field(min_length=1, max_length=30)
    emergency_path: str = Str


class PlanContent(Strict):
    title: str = ShortStr
    implementation_revision_id: str = ShortStr
    target_scope_id: str = ShortStr
    rings: list[RingSpec] = Field(min_length=1, max_length=4)
    pause_criteria: list[str] = Field(min_length=1, max_length=20)
    rollback_plan: RollbackPlan
    prerequisite_changes: list[dict[str, Any]] = Field(default_factory=list, max_length=30)
    deployment_instructions: str = Str


class PlanCreate(PlanContent):
    control_id: str = ShortStr


class PlanUpdate(PlanContent):
    expected_lock_version: int


class PackageCreate(Strict):
    through_stage: Literal["OBSERVATION", "PILOT", "LIMITED", "BROAD"]


class PackageDecision(Strict):
    decision: Literal["APPROVE", "REJECT"]
    role: Literal["SECURITY_APPROVER", "CLOUD_ENGINEER"]
    expected_digest: str = Field(min_length=10, max_length=80)
    rationale: str = Field(min_length=10, max_length=4000)


class AdvanceCommand(Strict):
    to_stage: Literal["OBSERVATION", "PILOT", "LIMITED", "BROAD"]
    expected_lock_version: int
    reason: str = Str


class PlanStateCommand(Strict):
    reason: str = Str
    expected_lock_version: int


class AppliedIdentity(Strict):
    native_identity: str = Field(min_length=1, max_length=512)
    config_digest: str = Field(min_length=1, max_length=80)
    native_policy_id: str | None = Field(default=None, max_length=128)


class ReceiptPayload(Strict):
    receipt_id: str = Field(min_length=1, max_length=128)
    provenance: Literal["MOCK", "LIVE"]
    bundle_digest: str = Field(min_length=1, max_length=80)
    ring: Literal["OBSERVATION", "PILOT", "LIMITED", "BROAD"]
    target_scope_native_id: str = Field(min_length=1, max_length=512)
    pipeline_run_ref: str = Field(min_length=1, max_length=256)
    result: Literal["ACCEPTED", "APPLIED", "FAILED"]
    applied_native_identities: list[AppliedIdentity] = Field(default_factory=list, max_length=50)
    started_at: datetime
    completed_at: datetime
    error: str | None = Field(default=None, max_length=2000)


class MockPipelineRun(Strict):
    bundle_id: str = ShortStr
    ring: Literal["OBSERVATION", "PILOT", "LIMITED", "BROAD"]
    scenario: Literal["SUCCESS", "FAILURE", "STALE", "MISMATCH"]


class MockObservation(Strict):
    delivery_target_id: str = ShortStr
    scenario: Literal["MATCH", "DRIFT", "MISSING"]
    observed_at: datetime | None = None


class BindingObservation(Strict):
    scenario: Literal["MATCH", "DRIFT", "MISSING"]


class SettingUpdate(Strict):
    value: dict[str, Any]
    expected_lock_version: int


class RoleGrant(Strict):
    user_id: str = ShortStr
    role: Literal["VIEWER", "CONTROL_ENGINEER", "EXCEPTION_REQUESTER", "SECURITY_APPROVER", "CLOUD_ENGINEER", "ADMIN"]
    scope_id: str | None = None


class DevLogin(Strict):
    username: str = Field(min_length=1, max_length=64)
