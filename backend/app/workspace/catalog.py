"""The approved A2UI component catalog for the AI Control Workspace.

Two layers of typed contracts, both strict (unknown fields are rejected):

* **Component props** travel in A2UI ``updateComponents`` messages. Domain components carry only a
  plain-text ``title`` and a data binding (``{"path": "/views/<name>"}``); they never carry free-form
  markup, styles, scripts or URLs.
* **Data contracts** describe the values the server places in the surface data model with
  ``updateDataModel``. Every value is built by the server from existing services; the model in
  AI-assisted mode never writes them.

The frontend mirrors these contracts in ``frontend/src/components/workspace/catalog/contracts.ts``
and the generated summary in ``docs/a2ui-catalog.json`` keeps the two in step (tested on both sides).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CATALOG_ID = "urn:ccp:a2ui:control-workspace:v1"
PROTOCOL_VERSION = "v0.9"

# Internal Classic/workspace routes only. No scheme, no host, no protocol-relative URLs, no "." or ".." segments.
SAFE_HREF = (r"^/(dashboard|controls|assessments|exceptions|rollouts|bundles|implementations|audit|workspace)"
             r"(/[A-Za-z0-9_~:@-][A-Za-z0-9._~:@-]*)*(\?[A-Za-z0-9._~:@=&%+/-]*)?$")
DATA_PATH = r"^/views/[a-z][a-z0-9_]{0,39}$"
COMPONENT_ID = r"^[A-Za-z][A-Za-z0-9_-]{0,63}$"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


Text = Field(max_length=4000)
Label = Field(max_length=256)


class Link(Contract):
    label: str = Field(min_length=1, max_length=120)
    href: str = Field(pattern=SAFE_HREF, max_length=512)


# ------------------------------------------------------------------------------------ component props


class DataBinding(Contract):
    path: str = Field(pattern=DATA_PATH)


class DomainProps(Contract):
    title: str = Field(min_length=1, max_length=120)
    data: DataBinding


class CanvasStackProps(Contract):
    children: list[str] = Field(min_length=1, max_length=40)


class CanvasNoticeProps(Contract):
    tone: Literal["info", "warning", "critical"]
    text: str = Field(min_length=1, max_length=1000)


# ------------------------------------------------------------------------------------ data contracts


class NextDecision(Contract):
    decision: str = Label
    owner_role: str = Field(max_length=64)
    detail: str = Text


class ImplementationSummary(Contract):
    id: str = Label
    name: str = Label
    provider: str = Field(max_length=16)
    mechanism_role: str = Field(max_length=64)
    latest_revision_id: str = Label
    latest_revision: int
    status: str = Field(max_length=32)
    validation_outcome: str | None = Field(default=None, max_length=64)
    integration_ready: bool | None = None
    verification_status: str | None = Field(default=None, max_length=64)
    source_ref: str = Field(max_length=512)
    pinned_version: str | None = Field(default=None, max_length=64)


class ControlSummaryData(Contract):
    control_id: str = Label
    name: str = Label
    revision: int
    revision_id: str = Label
    status: str = Field(max_length=32)
    severity: str = Field(max_length=16)
    origin: str = Field(max_length=32)
    operational_owner: str = Label
    providers: list[str]
    resource_types: list[str]
    security_objective: str = Text
    prevention_boundary: str = Text
    limitations: list[str]
    exception_eligible: bool
    security_owner: str = Label
    engineering_owner: str = Label
    implementations: list[ImplementationSummary]
    next_decision: NextDecision | None
    links: list[Link]


class CoverageColumn(Contract):
    key: str = Field(max_length=64)
    label: str = Field(max_length=64)


class CoverageRow(Contract):
    scope_id: str = Label
    scope_name: str = Label
    environment: str | None = Field(default=None, max_length=32)
    cells: dict[str, int]


class ControlCoverageMatrixData(Contract):
    available: bool
    unavailable_reason: str | None = Field(default=None, max_length=1000)
    assessment_run_id: str | None = Field(default=None, max_length=256)
    unit: str = Label
    denominator: str = Text
    columns: list[CoverageColumn]
    rows: list[CoverageRow]
    totals: dict[str, int]
    verified_protected_ratio: str = Field(max_length=32)
    links: list[Link]


class ConfidenceItem(Contract):
    category: str = Field(max_length=16)
    reasons: list[str]


class RequestImpactSummary(Contract):
    evidence_present: bool
    total: int
    counts: dict[str, int] | None
    note: str = Text


class RunAction(Contract):
    implementation_revision_id: str = Label
    target_scope_id: str = Label


class ImpactAssessmentData(Contract):
    available: bool
    unavailable_reason: str | None = Field(default=None, max_length=1000)
    requested_scope_id: str | None = Field(default=None, max_length=256)
    run_id: str | None = Field(default=None, max_length=256)
    status: str | None = Field(default=None, max_length=32)
    target_scope_id: str | None = Field(default=None, max_length=256)
    filtered_to_scope: bool = False
    filter_note: str | None = Field(default=None, max_length=1000)
    assessed_at: str | None = Field(default=None, max_length=64)
    evaluator: str | None = Field(default=None, max_length=256)
    basis_current: bool | None = None
    basis_notes: list[str] = Field(default_factory=list)
    configuration: dict[str, int] | None = None
    total_resources: int | None = None
    reconciles: bool | None = None
    exceptions: dict[str, int] | None = None
    request_impact: RequestImpactSummary | None = None
    readiness: dict[str, int] | None = None
    newly_preventive_resources: int | None = None
    confidence: dict[str, ConfidenceItem] | None = None
    disclosure: str | None = Field(default=None, max_length=4000)
    data_provenance: str | None = Field(default=None, max_length=64)
    can_run: bool = False
    run_blocked_reason: str | None = Field(default=None, max_length=1000)
    run_action: RunAction | None = None
    links: list[Link]


class ResourceRow(Contract):
    resource_id: str = Field(max_length=512)
    name: str = Label
    scope_id: str | None = Field(default=None, max_length=256)
    resource_type: str | None = Field(default=None, max_length=256)
    application: str | None = Field(default=None, max_length=256)
    owner: str | None = Field(default=None, max_length=256)
    applicability: str | None = Field(default=None, max_length=32)
    configuration_result: str | None = Field(default=None, max_length=32)
    exception_disposition: str | None = Field(default=None, max_length=32)
    exception_id: str | None = Field(default=None, max_length=256)
    readiness: str | None = Field(default=None, max_length=32)
    reasons: list[str]
    missing_fields: list[str]
    can_prepare_exception: bool


class ResourceImpactTableData(Contract):
    available: bool
    control_id: str = Label
    run_id: str | None = Field(default=None, max_length=256)
    scope_id: str | None = Field(default=None, max_length=256)
    total: int
    truncated: bool
    rows: list[ResourceRow]
    links: list[Link]


class ReadinessBlocker(Contract):
    kind: str = Field(max_length=64)
    message: str = Text
    resolution: str = Text
    owner_team: str = Label
    scope_id: str | None = Field(default=None, max_length=256)


class ApplicationReadiness(Contract):
    application: str = Label
    readiness: str = Field(max_length=32)
    resources: int
    blockers: list[ReadinessBlocker]


class EvidenceRecord(Contract):
    id: str = Label
    application: str = Label
    scope_id: str = Label
    prerequisite: str = Field(max_length=64)
    status: str = Field(max_length=32)
    collected_at: str = Field(max_length=64)
    stale: bool
    provided_by: str = Label
    provenance: str = Field(max_length=32)


class ApplicationReadinessPanelData(Contract):
    available: bool
    run_id: str | None = Field(default=None, max_length=256)
    applications: list[ApplicationReadiness]
    evidence: list[EvidenceRecord]
    note: str = Text


class BaselineBinding(Contract):
    binding_id: str = Label
    native_id: str = Field(max_length=512)
    scope_id: str = Label
    effect: str | None = Field(default=None, max_length=64)
    enforcement_mode: str | None = Field(default=None, max_length=64)
    management: str = Field(max_length=64)
    origin: str = Field(max_length=64)
    observed_at: str | None = Field(default=None, max_length=64)
    provenance: str | None = Field(default=None, max_length=64)


class ProposedImplementation(Contract):
    implementation_revision_id: str = Label
    policy_kind: str = Field(max_length=64)
    source_ref: str = Field(max_length=512)
    pinned_version: str | None = Field(default=None, max_length=64)
    content_digest: str | None = Field(default=None, max_length=128)
    parameters: dict[str, Any]
    assignment_settings: dict[str, Any]
    verification: str = Field(max_length=64)


class PolicyChange(Contract):
    field: str = Field(max_length=128)
    scope_id: str | None = Field(default=None, max_length=256)
    baseline: str = Field(max_length=512)
    proposed: str = Field(max_length=512)


class PolicyDiffViewerData(Contract):
    baseline: list[BaselineBinding]
    proposed: ProposedImplementation | None
    changes: list[PolicyChange]
    baseline_coverage_counts: dict[str, int]
    note: str = Text


class ScopeOption(Contract):
    scope_id: str = Label
    name: str = Label
    scope_type: str = Field(max_length=64)
    environment: str | None = Field(default=None, max_length=32)
    depth: int


class ScopeSelectorData(Contract):
    provider: str | None = Field(default=None, max_length=16)
    selected_scope_id: str | None = Field(default=None, max_length=256)
    options: list[ScopeOption]
    note: str = Text


class ExceptionItem(Contract):
    id: str = Label
    scope_id: str = Label
    application: str = Label
    granularity: str = Field(max_length=32)
    resource_ids: list[str]
    effective_status: str = Field(max_length=32)
    native_status: str = Field(max_length=32)
    disposition: str = Field(max_length=32)
    representability: str = Field(max_length=64)
    expires_at: str | None = Field(default=None, max_length=64)
    days_until_expiry: float
    expiring_soon: bool
    requester_id: str = Label
    href: str = Field(pattern=SAFE_HREF, max_length=512)


class ExceptionReviewData(Contract):
    items: list[ExceptionItem]
    counts_by_status: dict[str, int]
    note: str = Text
    links: list[Link]


class TimelineStage(Contract):
    stage: str = Field(max_length=32)
    status: Literal["DONE", "CURRENT", "PLANNED", "NOT_PLANNED"]
    scope_ids: list[str]
    settings_summary: str = Field(max_length=512)


class PlanRef(Contract):
    id: str = Label
    title: str = Label
    stage: str = Field(max_length=32)
    state: str = Field(max_length=32)
    target_scope_id: str = Label
    href: str = Field(pattern=SAFE_HREF, max_length=512)


class RolloutTimelineData(Contract):
    plan: PlanRef | None
    suggested: bool
    stages: list[TimelineStage]
    note: str = Text


class PackageRef(Contract):
    id: str = Label
    revision: int
    status: str = Field(max_length=32)
    manifest_digest: str = Field(max_length=128)
    through_stage: str = Field(max_length=32)
    href: str = Field(pattern=SAFE_HREF, max_length=512)


class DecisionRow(Contract):
    role: str = Field(max_length=64)
    actor_id: str = Label
    decision: str = Field(max_length=32)
    decided_at: str | None = Field(default=None, max_length=64)
    bound_to_current_digest: bool


class GateRow(Contract):
    id: str = Field(max_length=128)
    label: str = Label
    phase: str = Field(max_length=32)
    detail: str = Text


class ApprovalStatusData(Contract):
    package: PackageRef | None
    required_roles: list[str]
    decisions: list[DecisionRow]
    failing_gates: list[GateRow]
    note: str = Text


class EvidenceSource(Contract):
    kind: str = Field(max_length=64)
    id: str = Field(max_length=256)
    label: str = Label
    provenance: str | None = Field(default=None, max_length=64)
    collected_at: str | None = Field(default=None, max_length=64)
    digest: str | None = Field(default=None, max_length=128)
    stale: bool | None = None


class EvidencePanelData(Contract):
    demo_data: bool
    sources: list[EvidenceSource]
    confidence: dict[str, ConfidenceItem] | None
    disclosure: str | None = Field(default=None, max_length=4000)
    limitations: list[str]
    assumptions: list[str]


class BundleFile(Contract):
    path: str = Field(max_length=512)
    sha256: str = Field(max_length=128)


class BundleRef(Contract):
    id: str = Label
    kind: str = Field(max_length=32)
    banner: str = Field(max_length=512)
    bundle_digest: str = Field(max_length=128)
    exported_at: str | None = Field(default=None, max_length=64)
    files: list[BundleFile]
    href: str = Field(pattern=SAFE_HREF, max_length=512)


class GitOpsHandoffPreviewData(Contract):
    bundle: BundleRef | None
    package_status: str | None = Field(default=None, max_length=32)
    note: str = Text


DOMAIN_COMPONENTS: dict[str, type[Contract]] = {
    "ControlSummary": ControlSummaryData,
    "ControlCoverageMatrix": ControlCoverageMatrixData,
    "ImpactAssessment": ImpactAssessmentData,
    "ResourceImpactTable": ResourceImpactTableData,
    "ApplicationReadinessPanel": ApplicationReadinessPanelData,
    "PolicyDiffViewer": PolicyDiffViewerData,
    "ScopeSelector": ScopeSelectorData,
    "ExceptionReview": ExceptionReviewData,
    "RolloutTimeline": RolloutTimelineData,
    "ApprovalStatus": ApprovalStatusData,
    "EvidencePanel": EvidencePanelData,
    "GitOpsHandoffPreview": GitOpsHandoffPreviewData,
}

# Layout primitives owned by this catalog (no basic-catalog components are accepted).
LAYOUT_COMPONENTS: dict[str, type[Contract]] = {
    "CanvasStack": CanvasStackProps,
    "CanvasNotice": CanvasNoticeProps,
}

COMPONENT_DESCRIPTIONS: dict[str, str] = {
    "ControlSummary": "Security intent, revision status, prevention boundary, implementations and next decision.",
    "ControlCoverageMatrix": "Per-scope coverage from the latest completed assessment, with stated denominator.",
    "ImpactAssessment": "Persisted impact assessment rollup (configuration, exceptions, requests, readiness).",
    "ResourceImpactTable": "Per-resource results from a persisted assessment run.",
    "ApplicationReadinessPanel": "Readiness by application, open blockers and supplied evidence.",
    "PolicyDiffViewer": "Existing native bindings (baseline) versus the proposed implementation revision.",
    "ScopeSelector": "Scopes the current identity may read; changing it re-runs the investigation.",
    "ExceptionReview": "Exceptions with governance vs native status, disposition and expiry.",
    "RolloutTimeline": "Rollout rings and stage for the active plan, or the unpersisted suggested template.",
    "ApprovalStatus": "Change package status, digest-bound decisions and failing gates (read-only).",
    "EvidencePanel": "Data sources, provenance, freshness, confidence and limitations.",
    "GitOpsHandoffPreview": "Exported handoff bundle files and digests (approved does not mean deployed).",
    "CanvasStack": "Vertical layout container (children are component ids).",
    "CanvasNotice": "Plain-text notice with an info, warning or critical tone.",
}

ALL_COMPONENTS: dict[str, type[Contract]] = {**DOMAIN_COMPONENTS, **LAYOUT_COMPONENTS}

# Client-side actions a component may emit. The client routes them; anything else is ignored.
CLIENT_ACTIONS: dict[str, str] = {
    "ws.select_scope": "Re-run the investigation for another readable scope (read-only).",
    "ws.run_assessment": "Ask the user to confirm, then call the existing POST /api/v1/assessments.",
    "ws.prepare_exception_draft": "Prepare an unsaved exception draft for a resource (read-only).",
}


def props_model(component: str) -> type[Contract]:
    if component in DOMAIN_COMPONENTS:
        return DomainProps
    return LAYOUT_COMPONENTS[component]


def catalog_document() -> dict[str, Any]:
    """Machine-readable catalog summary (also written to docs/a2ui-catalog.json)."""
    components = {}
    for name in sorted(ALL_COMPONENTS):
        entry: dict[str, Any] = {
            "description": COMPONENT_DESCRIPTIONS[name],
            "kind": "domain" if name in DOMAIN_COMPONENTS else "layout",
            "props_schema": props_model(name).model_json_schema(),
        }
        if name in DOMAIN_COMPONENTS:
            entry["data_schema"] = DOMAIN_COMPONENTS[name].model_json_schema()
            entry["data_required"] = sorted(
                k for k, f in DOMAIN_COMPONENTS[name].model_fields.items() if f.is_required())
        components[name] = entry
    return {
        "catalogId": CATALOG_ID,
        "protocolVersion": PROTOCOL_VERSION,
        "renderer": "@a2ui/react 0.11.1 / @a2ui/web_core 0.11.0 (A2UI v0.9 message processor)",
        "components": components,
        "clientActions": CLIENT_ACTIONS,
        "safeHrefPattern": SAFE_HREF,
        "dataPathPattern": DATA_PATH,
    }
