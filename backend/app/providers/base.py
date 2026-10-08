"""Provider capability contract.

Providers do not pretend to share semantics. Each declares what it can express, how it can be
observed, and what it cannot do. None of these interfaces mutate cloud infrastructure.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.models.entities import ImplementationRevision, PolicyBinding, ResourceSnapshot, SecurityException
from app.services.scopes import ScopeTree


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    policy_kinds: list[str]
    supported_scope_types: list[str]
    supported_effects: list[str]
    native_audit_mode: str
    observation_method: str
    validation_methods: list[str]
    assessment_limitations: list[str]
    exemption_mechanism: str
    exemption_granularities: dict[str, str]
    native_expiry_supported: bool
    artifact_kinds: list[str]
    ring_settings: dict[str, str]
    integration_status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Check:
    id: str
    status: str  # PASS | FAIL | WARN | UNSUPPORTED | REQUIRES_DIFFERENT_MECHANISM
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    outcome: str
    checks: list[Check]
    evaluator_id: str | None
    evaluator_version: str | None
    test_results: list[dict[str, Any]]
    integration_ready: bool
    integration_blockers: list[str]


@dataclass
class ResourceEvaluation:
    applicability: str
    configuration_result: str
    reasons: list[str]
    evidence: dict[str, Any]
    missing_fields: list[str]
    enforcement_applies: bool = True


@dataclass
class RequestEvaluation:
    impact: str
    reasons: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)
    baseline: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExceptionRepresentation:
    representability: str
    reason: str
    native_representation: dict[str, Any] | None
    native_expiry_supported: bool


@dataclass
class Artifact:
    path: str
    kind: str
    ring_stage: str
    scope_id: str
    native_identity: str
    content: dict[str, Any]
    enforcing: bool
    exception_id: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Comparison:
    result: str  # MATCH | MISMATCH | MISSING
    differences: list[str]


@dataclass
class AssessmentContext:
    tree: ScopeTree
    target_scope_id: str
    target_scope_ids: set[str]
    bindings: list[PolicyBinding]
    now: datetime
    excluded_scope_ids: set[str] = field(default_factory=set)
    effective_principal_exceptions: list[SecurityException] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


class PolicyProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...

    def validate_implementation(self, revision: ImplementationRevision, tree: ScopeTree,
                                bindings: list[PolicyBinding]) -> ValidationReport: ...

    def assess_inventory(self, revision: ImplementationRevision, resources: list[ResourceSnapshot],
                         context: AssessmentContext) -> dict[str, ResourceEvaluation]: ...

    def evaluate_supported_request_fixture(self, revision: ImplementationRevision, request: dict[str, Any],
                                           context: AssessmentContext,
                                           resource_lookup: dict[str, ResourceSnapshot],
                                           exempt_resource_ids: set[str]) -> RequestEvaluation: ...

    def validate_exception_representation(self, exception: SecurityException, revision: ImplementationRevision,
                                          bindings: list[PolicyBinding], tree: ScopeTree
                                          ) -> ExceptionRepresentation: ...

    def generate_change_artifacts(self, package: dict[str, Any], revision: ImplementationRevision,
                                  tree: ScopeTree) -> list[Artifact]: ...

    def compare_observed_state(self, expected: dict[str, Any], observed: dict[str, Any] | None,
                               artifact_kind: str) -> Comparison: ...

    def validate_ring_settings(self, stage: str, settings: dict[str, Any],
                               revision: ImplementationRevision) -> list[Check]: ...

    def ring_is_enforcing(self, settings: dict[str, Any]) -> bool: ...

    def readiness_prerequisites(self) -> list[dict[str, str]]: ...

    def exception_covers_resource(self, exception: SecurityException, resource: ResourceSnapshot,
                                  tree: ScopeTree) -> bool: ...

    def mock_observed_state(self, expected: dict[str, Any], scenario: str, artifact_kind: str
                            ) -> dict[str, Any] | None: ...


def outcome_from_checks(checks: list[Check]) -> str:
    statuses = {c.status for c in checks}
    if "REQUIRES_DIFFERENT_MECHANISM" in statuses:
        return "REQUIRES_DIFFERENT_MECHANISM"
    if "UNSUPPORTED" in statuses:
        return "UNSUPPORTED"
    if "FAIL" in statuses:
        return "FAIL"
    return "PASS"
