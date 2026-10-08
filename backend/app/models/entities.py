"""Relational schema.

Design notes:
- Security intent (ControlRevision) never contains provider syntax.
- Native policies (ImplementationRevision) and their assignments/attachments (PolicyBinding)
  are separate rows, so one implementation can be observed in audit at one scope and
  enforced at another. There is no global enforcement status on a control.
- Submitted revisions, snapshots, assessment results, decisions, receipts, observations and
  audit events are immutable; database triggers in the initial migration back this up.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def ts(nullable: bool = False) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=nullable)


# --------------------------------------------------------------------------------------
# Identity and configuration
# --------------------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(256))
    team: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = ts()
    # UI preferences only (e.g. preferred experience). Never used for authorization.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    role_assignments: Mapped[list[RoleAssignment]] = relationship(
        back_populates="user", foreign_keys="RoleAssignment.user_id"
    )


class RoleAssignment(Base):
    __tablename__ = "role_assignments"
    __table_args__ = (UniqueConstraint("user_id", "role", "scope_id", name="uq_role_assignment"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(32))
    # NULL scope means all scopes. Otherwise the scope and its descendants.
    scope_id: Mapped[str | None] = mapped_column(ForeignKey("scopes.id"), nullable=True)
    created_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    user: Mapped[User] = relationship(back_populates="role_assignments", foreign_keys=[user_id])


class GovernanceSetting(Base):
    __tablename__ = "governance_settings"
    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    description: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = ts()
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)


# --------------------------------------------------------------------------------------
# Scopes and inventory
# --------------------------------------------------------------------------------------


class Scope(Base):
    __tablename__ = "scopes"
    __table_args__ = (UniqueConstraint("provider", "native_id", name="uq_scope_native"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    native_id: Mapped[str] = mapped_column(String(512))
    scope_type: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("scopes.id"), nullable=True)
    display_name: Mapped[str] = mapped_column(String(256))
    environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    business_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    application: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_management_account: Mapped[bool] = mapped_column(Boolean, default=False)
    provenance: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(256))
    provenance: Mapped[str] = mapped_column(String(32))
    collected_at: Mapped[datetime] = ts()
    content_digest: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = ts()


class ResourceSnapshot(Base):
    __tablename__ = "resource_snapshots"
    __table_args__ = (UniqueConstraint("snapshot_id", "resource_id", name="uq_resource_in_snapshot"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshots.id"), index=True)
    resource_id: Mapped[str] = mapped_column(String(512))
    name: Mapped[str] = mapped_column(String(256))
    provider: Mapped[str] = mapped_column(String(16))
    resource_type: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSONB)
    application: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    criticality: Mapped[str | None] = mapped_column(String(32), nullable=True)
    collected_at: Mapped[datetime] = ts()
    source: Mapped[str] = mapped_column(String(256))
    missing_fields: Mapped[list[Any]] = mapped_column(JSONB, default=list)


class RequestFixtureSet(Base):
    __tablename__ = "request_fixture_sets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    label: Mapped[str] = mapped_column(String(256))
    provenance: Mapped[str] = mapped_column(String(32))
    items: Mapped[list[Any]] = mapped_column(JSONB)
    content_digest: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = ts()


class ReadinessEvidence(Base):
    """Evidence supplied by application or engineering teams. Append-only; newest wins."""

    __tablename__ = "readiness_evidence"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    application: Mapped[str] = mapped_column(String(128))
    scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    prerequisite: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    evidence_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provided_by: Mapped[str] = mapped_column(String(256))
    provenance: Mapped[str] = mapped_column(String(32))
    collected_at: Mapped[datetime] = ts()
    created_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class WorkReference(Base):
    """Lightweight link to work owned by another team. Not a workflow engine."""

    __tablename__ = "work_references"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    owner_team: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    detail: Mapped[str] = mapped_column(Text, default="")
    external_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    related_type: Mapped[str] = mapped_column(String(64))
    related_id: Mapped[str] = mapped_column(String(64))
    scope_id: Mapped[str | None] = mapped_column(ForeignKey("scopes.id"), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(256), unique=True, nullable=True)
    created_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closed_at: Mapped[datetime | None] = ts(nullable=True)


# --------------------------------------------------------------------------------------
# Controls and implementations
# --------------------------------------------------------------------------------------


class Control(Base):
    __tablename__ = "controls"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    origin: Mapped[str] = mapped_column(String(32))
    # Cataloguing an existing control never transfers operational ownership.
    operational_owner: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revisions: Mapped[list[ControlRevision]] = relationship(
        back_populates="control", order_by="ControlRevision.revision"
    )
    implementations: Mapped[list[Implementation]] = relationship(back_populates="control")


class ControlRevision(Base):
    __tablename__ = "control_revisions"
    __table_args__ = (UniqueConstraint("control_id", "revision", name="uq_control_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text)
    security_objective: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    source_evidence: Mapped[list[Any]] = mapped_column(JSONB)
    severity: Mapped[str] = mapped_column(String(16))
    providers: Mapped[list[Any]] = mapped_column(JSONB)
    resource_types: Mapped[list[Any]] = mapped_column(JSONB)
    applicability_criteria: Mapped[str] = mapped_column(Text)
    security_owner: Mapped[str] = mapped_column(String(256))
    engineering_owner: Mapped[str] = mapped_column(String(256))
    framework_refs: Mapped[list[Any]] = mapped_column(JSONB)
    exception_eligible: Mapped[bool] = mapped_column(Boolean)
    prevention_boundary: Mapped[str] = mapped_column(Text)
    limitations: Mapped[list[Any]] = mapped_column(JSONB)
    change_reason: Mapped[str] = mapped_column(Text)
    content_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    submitted_at: Mapped[datetime | None] = ts(nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = ts(nullable=True)
    superseded_at: Mapped[datetime | None] = ts(nullable=True)
    retired_at: Mapped[datetime | None] = ts(nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    control: Mapped[Control] = relationship(back_populates="revisions")

    __mapper_args__ = {"version_id_col": lock_version}


class Implementation(Base):
    __tablename__ = "implementations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(256))
    mechanism_role: Mapped[str] = mapped_column(String(64))
    management: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    control: Mapped[Control] = relationship(back_populates="implementations")
    revisions: Mapped[list[ImplementationRevision]] = relationship(
        back_populates="implementation", order_by="ImplementationRevision.revision"
    )


class ImplementationRevision(Base):
    __tablename__ = "implementation_revisions"
    __table_args__ = (UniqueConstraint("implementation_id", "revision", name="uq_impl_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    implementation_id: Mapped[str] = mapped_column(ForeignKey("implementations.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    control_revision_id: Mapped[str] = mapped_column(ForeignKey("control_revisions.id"))
    status: Mapped[str] = mapped_column(String(16))
    policy_kind: Mapped[str] = mapped_column(String(64))
    source_kind: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(512))
    pinned_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    assignment_settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    native_document: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    capability_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prerequisites: Mapped[list[Any]] = mapped_column(JSONB)
    limitations: Mapped[list[Any]] = mapped_column(JSONB)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    change_reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    submitted_at: Mapped[datetime | None] = ts(nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = ts(nullable=True)
    superseded_at: Mapped[datetime | None] = ts(nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    implementation: Mapped[Implementation] = relationship(back_populates="revisions")

    __mapper_args__ = {"version_id_col": lock_version}


class ImplementationValidation(Base):
    """Validation and test results for an exact implementation content digest. Append-only."""

    __tablename__ = "implementation_validations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Monotonic insertion order: "latest" never depends on timestamps (the clock is testable/fixed).
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    implementation_revision_id: Mapped[str] = mapped_column(
        ForeignKey("implementation_revisions.id"), index=True
    )
    revision_digest: Mapped[str] = mapped_column(String(80))
    outcome: Mapped[str] = mapped_column(String(40))
    evaluator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluator_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    checks: Mapped[list[Any]] = mapped_column(JSONB)
    test_results: Mapped[list[Any]] = mapped_column(JSONB)
    possible_duplicates: Mapped[list[Any]] = mapped_column(JSONB)
    integration_ready: Mapped[bool] = mapped_column(Boolean)
    integration_blockers: Mapped[list[Any]] = mapped_column(JSONB)
    validator_version: Mapped[str] = mapped_column(String(32))
    validated_at: Mapped[datetime] = ts()
    validated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PolicyBinding(Base):
    """An actual assignment/attachment at a scope, separate from the policy definition."""

    __tablename__ = "policy_bindings"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    control_id: Mapped[str | None] = mapped_column(ForeignKey("controls.id"), nullable=True, index=True)
    implementation_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("implementation_revisions.id"), nullable=True
    )
    provider: Mapped[str] = mapped_column(String(16))
    binding_kind: Mapped[str] = mapped_column(String(48))
    native_id: Mapped[str] = mapped_column(String(512))
    definition_ref: Mapped[str] = mapped_column(String(512))
    definition_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    exclusions: Mapped[list[Any]] = mapped_column(JSONB)
    source_repository: Mapped[str | None] = mapped_column(String(512), nullable=True)
    management: Mapped[str] = mapped_column(String(32))
    origin: Mapped[str] = mapped_column(String(32))
    desired_state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    observed_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    observed_at: Mapped[datetime | None] = ts(nullable=True)
    evidence_provenance: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = ts()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


# --------------------------------------------------------------------------------------
# Assessments
# --------------------------------------------------------------------------------------


class AssessmentRun(Base):
    __tablename__ = "assessment_runs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    control_revision_id: Mapped[str] = mapped_column(ForeignKey("control_revisions.id"))
    implementation_revision_id: Mapped[str] = mapped_column(ForeignKey("implementation_revisions.id"))
    target_scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    inventory_snapshot_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshots.id"))
    request_fixture_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("request_fixture_sets.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16))
    evaluator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluator_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    disclosure: Mapped[str] = mapped_column(Text)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_digest: Mapped[str] = mapped_column(String(80))
    rollup: Mapped[dict[str, Any]] = mapped_column(JSONB)
    result_digest: Mapped[str] = mapped_column(String(80))
    blockers: Mapped[list[Any]] = mapped_column(JSONB)
    limitations: Mapped[list[Any]] = mapped_column(JSONB)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB)
    next_steps: Mapped[list[Any]] = mapped_column(JSONB)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    assessed_at: Mapped[datetime] = ts()
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class AssessmentResult(Base):
    __tablename__ = "assessment_results"
    __table_args__ = (Index("ix_assessment_results_run_kind", "run_id", "subject_kind"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("assessment_runs.id"))
    subject_kind: Mapped[str] = mapped_column(String(16))  # RESOURCE | REQUEST
    subject_id: Mapped[str] = mapped_column(String(512))
    subject_name: Mapped[str] = mapped_column(String(256))
    resource_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scope_id: Mapped[str | None] = mapped_column(ForeignKey("scopes.id"), nullable=True)
    application: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    applicability: Mapped[str | None] = mapped_column(String(16), nullable=True)
    configuration_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    exception_disposition: Mapped[str | None] = mapped_column(String(24), nullable=True)
    exception_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    request_impact: Mapped[str | None] = mapped_column(String(32), nullable=True)
    readiness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reasons: Mapped[list[Any]] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    missing_fields: Mapped[list[Any]] = mapped_column(JSONB)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSONB)


# --------------------------------------------------------------------------------------
# Exceptions and decisions
# --------------------------------------------------------------------------------------


class SecurityException(Base):
    __tablename__ = "exceptions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    lineage_id: Mapped[str] = mapped_column(String(64), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    renews_exception_id: Mapped[str | None] = mapped_column(ForeignKey("exceptions.id"), nullable=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    implementation_id: Mapped[str | None] = mapped_column(ForeignKey("implementations.id"), nullable=True)
    binding_ids: Mapped[list[Any]] = mapped_column(JSONB)
    requester_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    application: Mapped[str] = mapped_column(String(128))
    team: Mapped[str] = mapped_column(String(128))
    business_justification: Mapped[str] = mapped_column(Text)
    technical_justification: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    granularity: Mapped[str] = mapped_column(String(16))
    resource_ids: Mapped[list[Any]] = mapped_column(JSONB)
    principal_patterns: Mapped[list[Any]] = mapped_column(JSONB)
    risk_owner: Mapped[str] = mapped_column(String(256))
    compensating_controls: Mapped[list[Any]] = mapped_column(JSONB)
    valid_from: Mapped[datetime] = ts()
    expires_at: Mapped[datetime] = ts()
    governance_status: Mapped[str] = mapped_column(String(24))
    native_status: Mapped[str] = mapped_column(String(24))
    representability: Mapped[str] = mapped_column(String(40))
    representability_reason: Mapped[str] = mapped_column(Text)
    native_representation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    native_expiry_supported: Mapped[bool] = mapped_column(Boolean)
    native_observed: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    native_observed_at: Mapped[datetime | None] = ts(nullable=True)
    native_provenance: Mapped[str | None] = mapped_column(String(32), nullable=True)
    work_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ApprovalDecision(Base):
    """Immutable decision bound to the exact digest that was reviewed."""

    __tablename__ = "approval_decisions"
    __table_args__ = (Index("ix_decisions_subject", "subject_type", "subject_id"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject_type: Mapped[str] = mapped_column(String(32))  # CHANGE_PACKAGE | EXCEPTION
    subject_id: Mapped[str] = mapped_column(String(64))
    subject_digest: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))
    rationale: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime] = ts()
    correlation_id: Mapped[str] = mapped_column(String(64))


# --------------------------------------------------------------------------------------
# Rollout, packages, handoff, delivery
# --------------------------------------------------------------------------------------


class RolloutPlan(Base):
    __tablename__ = "rollout_plans"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    title: Mapped[str] = mapped_column(String(256))
    provider: Mapped[str] = mapped_column(String(16))
    target_scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    implementation_revision_ids: Mapped[list[Any]] = mapped_column(JSONB)
    rings: Mapped[list[Any]] = mapped_column(JSONB)
    pause_criteria: Mapped[list[Any]] = mapped_column(JSONB)
    rollback_plan: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prerequisite_changes: Mapped[list[Any]] = mapped_column(JSONB)
    deployment_instructions: Mapped[str] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = ts()
    updated_at: Mapped[datetime] = ts()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class ChangePackage(Base):
    """Immutable proposed change manifest. Approvals bind to manifest_digest."""

    __tablename__ = "change_packages"
    __table_args__ = (UniqueConstraint("plan_id", "package_revision", name="uq_package_revision"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("rollout_plans.id"), index=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    package_revision: Mapped[int] = mapped_column(Integer)
    through_stage: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    manifest_digest: Mapped[str] = mapped_column(String(80))
    gate_results: Mapped[list[Any]] = mapped_column(JSONB)
    status_reasons: Mapped[list[Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = ts()
    status_changed_at: Mapped[datetime] = ts()
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class HandoffBundle(Base):
    __tablename__ = "handoff_bundles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    package_id: Mapped[str] = mapped_column(ForeignKey("change_packages.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    bundle_digest: Mapped[str] = mapped_column(String(80), index=True)
    files: Mapped[dict[str, Any]] = mapped_column(JSONB)
    file_digests: Mapped[dict[str, Any]] = mapped_column(JSONB)
    adapter: Mapped[str] = mapped_column(String(32))
    export_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    export_checks: Mapped[list[Any]] = mapped_column(JSONB)
    exported_at: Mapped[datetime] = ts()
    exported_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class DeliveryTarget(Base):
    __tablename__ = "delivery_targets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(ForeignKey("handoff_bundles.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("rollout_plans.id"), index=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id"), index=True)
    ring_stage: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(ForeignKey("scopes.id"))
    artifact_kind: Mapped[str] = mapped_column(String(48))
    native_identity: Mapped[str] = mapped_column(String(512))
    expected_state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    expected_digest: Mapped[str] = mapped_column(String(80))
    enforcing: Mapped[bool] = mapped_column(Boolean)
    exception_id: Mapped[str | None] = mapped_column(ForeignKey("exceptions.id"), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("policy_bindings.id"), nullable=True)
    state: Mapped[str] = mapped_column(String(16))
    state_changed_at: Mapped[datetime] = ts()
    state_basis_at: Mapped[datetime | None] = ts(nullable=True)
    last_receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_observation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = ts(nullable=True)
    provenance: Mapped[str] = mapped_column(String(32))
    lock_version: Mapped[int] = mapped_column(Integer, default=1)

    __mapper_args__ = {"version_id_col": lock_version}


class DeploymentReceipt(Base):
    __tablename__ = "deployment_receipts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    receipt_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload_digest: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    bundle_id: Mapped[str | None] = mapped_column(ForeignKey("handoff_bundles.id"), nullable=True)
    bundle_digest: Mapped[str] = mapped_column(String(80))
    ring_stage: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_scope_native_id: Mapped[str] = mapped_column(String(512))
    pipeline_run_ref: Mapped[str] = mapped_column(String(256))
    applied_native_identities: Mapped[list[Any]] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(String(16))
    reported_completed_at: Mapped[datetime] = ts()
    received_at: Mapped[datetime] = ts()
    provenance: Mapped[str] = mapped_column(String(32))
    validation_status: Mapped[str] = mapped_column(String(32))
    validation_notes: Mapped[list[Any]] = mapped_column(JSONB)
    delivery_target_ids: Mapped[list[Any]] = mapped_column(JSONB)


class Observation(Base):
    __tablename__ = "observations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    delivery_target_id: Mapped[str | None] = mapped_column(ForeignKey("delivery_targets.id"), nullable=True)
    binding_id: Mapped[str | None] = mapped_column(ForeignKey("policy_bindings.id"), nullable=True)
    exception_id: Mapped[str | None] = mapped_column(ForeignKey("exceptions.id"), nullable=True)
    native_identity: Mapped[str] = mapped_column(String(512))
    observed_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    observed_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    observed_at: Mapped[datetime] = ts()
    recorded_at: Mapped[datetime] = ts()
    source: Mapped[str] = mapped_column(String(128))
    provenance: Mapped[str] = mapped_column(String(32))
    comparison: Mapped[str] = mapped_column(String(16))
    differences: Mapped[list[Any]] = mapped_column(JSONB)
    applied_to_state: Mapped[bool] = mapped_column(Boolean)
    notes: Mapped[list[Any]] = mapped_column(JSONB)


# --------------------------------------------------------------------------------------
# Audit
# --------------------------------------------------------------------------------------


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_correlation", "correlation_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = ts()
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_roles: Mapped[list[Any]] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(String(96))
    object_type: Mapped[str] = mapped_column(String(48))
    object_id: Mapped[str] = mapped_column(String(128))
    object_revision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    before_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    after_digest: Mapped[str | None] = mapped_column(String(80), nullable=True)
    scope_ids: Mapped[list[Any]] = mapped_column(JSONB)
    control_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
