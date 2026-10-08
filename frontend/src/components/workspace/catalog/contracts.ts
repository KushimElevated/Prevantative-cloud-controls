// Client mirror of backend/app/workspace/catalog.py (kept in step by tests/unit/workspace-catalog.test.tsx,
// which compares against the generated docs/a2ui-catalog.json). The server validates every payload first;
// these contracts are defence in depth and the only shapes the canvas components will render.
import { z } from "zod";
import { CommonSchemas } from "@a2ui/web_core/v0_9";
import type { CanvasActionName } from "../canvas-types";

export const CATALOG_ID = "urn:ccp:a2ui:control-workspace:v1";
export const PROTOCOL_VERSION = "v0.9";

// Internal Classic/workspace routes only. No scheme, no host, no protocol-relative URLs.
export const SAFE_HREF =
  /^\/(dashboard|controls|assessments|exceptions|rollouts|bundles|implementations|audit|workspace)(\/[A-Za-z0-9_~:@-][A-Za-z0-9._~:@-]*)*(\?[A-Za-z0-9._~:@=&%+/-]*)?$/;
export const DATA_PATH = /^\/views\/[a-z][a-z0-9_]{0,39}$/;
export const COMPONENT_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
export const SURFACE_ID = /^[A-Za-z0-9_-]{1,80}$/;
export const POINTER = /^(\/|(\/[A-Za-z0-9_-]{1,64}){1,4})$/;
export const MAX_MESSAGES = 20;
export const MAX_COMPONENTS = 60;
export const MAX_DATA_BYTES = 512_000;

// Same patterns as the backend tool inputs (tools.py Id / ResourceId).
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const RESOURCE_ID = /^[A-Za-z0-9/][A-Za-z0-9._:/@-]{0,511}$/;

/* ------------------------------------------------------------------ primitives */

const str = (max: number) => z.string().max(max);
const optStr = (max: number) => z.string().max(max).nullable().optional();
const label = str(256);
const text = str(4000);
const int = z.number().int();
const counts = z.record(z.string(), int);
const strings = z.array(z.string());
const href = z.string().max(512).regex(SAFE_HREF);

export const LinkSchema = z.object({ label: z.string().min(1).max(120), href });
export type LinkData = z.infer<typeof LinkSchema>;

/* ------------------------------------------------------------------ data contracts */

const NextDecision = z.object({ decision: label, owner_role: str(64), detail: text });

const ImplementationSummary = z.object({
  id: label,
  name: label,
  provider: str(16),
  mechanism_role: str(64),
  latest_revision_id: label,
  latest_revision: int,
  status: str(32),
  validation_outcome: optStr(64),
  integration_ready: z.boolean().nullable().optional(),
  verification_status: optStr(64),
  source_ref: str(512),
  pinned_version: optStr(64),
});

export const ControlSummaryData = z.object({
  control_id: label,
  name: label,
  revision: int,
  revision_id: label,
  status: str(32),
  severity: str(16),
  origin: str(32),
  operational_owner: label,
  providers: strings,
  resource_types: strings,
  security_objective: text,
  prevention_boundary: text,
  limitations: strings,
  exception_eligible: z.boolean(),
  security_owner: label,
  engineering_owner: label,
  implementations: z.array(ImplementationSummary),
  next_decision: NextDecision.nullable(),
  links: z.array(LinkSchema),
});

const CoverageColumn = z.object({ key: str(64), label: str(64) });
const CoverageRow = z.object({
  scope_id: label,
  scope_name: label,
  environment: optStr(32),
  cells: counts,
});

export const ControlCoverageMatrixData = z.object({
  available: z.boolean(),
  unavailable_reason: optStr(1000),
  assessment_run_id: optStr(256),
  unit: label,
  denominator: text,
  columns: z.array(CoverageColumn),
  rows: z.array(CoverageRow),
  totals: counts,
  verified_protected_ratio: str(32),
  links: z.array(LinkSchema),
});

export const ConfidenceItem = z.object({ category: str(16), reasons: strings });
const Confidence = z.record(z.string(), ConfidenceItem);

const RequestImpactSummary = z.object({
  evidence_present: z.boolean(),
  total: int,
  counts: counts.nullable(),
  note: text,
});

const RunAction = z.object({ implementation_revision_id: label, target_scope_id: label });

export const ImpactAssessmentData = z.object({
  available: z.boolean(),
  unavailable_reason: optStr(1000),
  requested_scope_id: optStr(256),
  run_id: optStr(256),
  status: optStr(32),
  target_scope_id: optStr(256),
  filtered_to_scope: z.boolean().optional().default(false),
  filter_note: optStr(1000),
  assessed_at: optStr(64),
  evaluator: optStr(256),
  basis_current: z.boolean().nullable().optional(),
  basis_notes: strings.optional().default([]),
  configuration: counts.nullable().optional(),
  total_resources: int.nullable().optional(),
  reconciles: z.boolean().nullable().optional(),
  exceptions: counts.nullable().optional(),
  request_impact: RequestImpactSummary.nullable().optional(),
  readiness: counts.nullable().optional(),
  newly_preventive_resources: int.nullable().optional(),
  confidence: Confidence.nullable().optional(),
  disclosure: optStr(4000),
  data_provenance: optStr(64),
  can_run: z.boolean().optional().default(false),
  run_blocked_reason: optStr(1000),
  run_action: RunAction.nullable().optional(),
  links: z.array(LinkSchema),
});

const ResourceRow = z.object({
  resource_id: str(512),
  name: label,
  scope_id: optStr(256),
  resource_type: optStr(256),
  application: optStr(256),
  owner: optStr(256),
  applicability: optStr(32),
  configuration_result: optStr(32),
  exception_disposition: optStr(32),
  exception_id: optStr(256),
  readiness: optStr(32),
  reasons: strings,
  missing_fields: strings,
  can_prepare_exception: z.boolean(),
});

export const ResourceImpactTableData = z.object({
  available: z.boolean(),
  control_id: label,
  run_id: optStr(256),
  scope_id: optStr(256),
  total: int,
  truncated: z.boolean(),
  rows: z.array(ResourceRow),
  links: z.array(LinkSchema),
});

const ReadinessBlocker = z.object({
  kind: str(64),
  message: text,
  resolution: text,
  owner_team: label,
  scope_id: optStr(256),
});

const ApplicationReadiness = z.object({
  application: label,
  readiness: str(32),
  resources: int,
  blockers: z.array(ReadinessBlocker),
});

const EvidenceRecord = z.object({
  id: label,
  application: label,
  scope_id: label,
  prerequisite: str(64),
  status: str(32),
  collected_at: str(64),
  stale: z.boolean(),
  provided_by: label,
  provenance: str(32),
});

export const ApplicationReadinessPanelData = z.object({
  available: z.boolean(),
  run_id: optStr(256),
  applications: z.array(ApplicationReadiness),
  evidence: z.array(EvidenceRecord),
  note: text,
});

const BaselineBinding = z.object({
  binding_id: label,
  native_id: str(512),
  scope_id: label,
  effect: optStr(64),
  enforcement_mode: optStr(64),
  management: str(64),
  origin: str(64),
  observed_at: optStr(64),
  provenance: optStr(64),
});

const ProposedImplementation = z.object({
  implementation_revision_id: label,
  policy_kind: str(64),
  source_ref: str(512),
  pinned_version: optStr(64),
  content_digest: optStr(128),
  parameters: z.record(z.string(), z.unknown()),
  assignment_settings: z.record(z.string(), z.unknown()),
  verification: str(64),
});

const PolicyChange = z.object({
  field: str(128),
  scope_id: optStr(256),
  baseline: str(512),
  proposed: str(512),
});

export const PolicyDiffViewerData = z.object({
  baseline: z.array(BaselineBinding),
  proposed: ProposedImplementation.nullable(),
  changes: z.array(PolicyChange),
  baseline_coverage_counts: counts,
  note: text,
});

const ScopeOption = z.object({
  scope_id: label,
  name: label,
  scope_type: str(64),
  environment: optStr(32),
  depth: int,
});

export const ScopeSelectorData = z.object({
  provider: optStr(16),
  selected_scope_id: optStr(256),
  options: z.array(ScopeOption),
  note: text,
});

const ExceptionItem = z.object({
  id: label,
  scope_id: label,
  application: label,
  granularity: str(32),
  resource_ids: strings,
  effective_status: str(32),
  native_status: str(32),
  disposition: str(32),
  representability: str(64),
  expires_at: optStr(64),
  days_until_expiry: z.number(),
  expiring_soon: z.boolean(),
  requester_id: label,
  href,
});

export const ExceptionReviewData = z.object({
  items: z.array(ExceptionItem),
  counts_by_status: counts,
  note: text,
  links: z.array(LinkSchema),
});

const TimelineStage = z.object({
  stage: str(32),
  status: z.enum(["DONE", "CURRENT", "PLANNED", "NOT_PLANNED"]),
  scope_ids: strings,
  settings_summary: str(512),
});

const PlanRef = z.object({
  id: label,
  title: label,
  stage: str(32),
  state: str(32),
  target_scope_id: label,
  href,
});

export const RolloutTimelineData = z.object({
  plan: PlanRef.nullable(),
  suggested: z.boolean(),
  stages: z.array(TimelineStage),
  note: text,
});

const PackageRef = z.object({
  id: label,
  revision: int,
  status: str(32),
  manifest_digest: str(128),
  through_stage: str(32),
  href,
});

const DecisionRow = z.object({
  role: str(64),
  actor_id: label,
  decision: str(32),
  decided_at: optStr(64),
  bound_to_current_digest: z.boolean(),
});

const GateRow = z.object({ id: str(128), label, phase: str(32), detail: text });

export const ApprovalStatusData = z.object({
  package: PackageRef.nullable(),
  required_roles: strings,
  decisions: z.array(DecisionRow),
  failing_gates: z.array(GateRow),
  note: text,
});

const EvidenceSource = z.object({
  kind: str(64),
  id: str(256),
  label,
  provenance: optStr(64),
  collected_at: optStr(64),
  digest: optStr(128),
  stale: z.boolean().nullable().optional(),
});

export const EvidencePanelData = z.object({
  demo_data: z.boolean(),
  sources: z.array(EvidenceSource),
  confidence: Confidence.nullable(),
  disclosure: optStr(4000),
  limitations: strings,
  assumptions: strings,
});

const BundleFile = z.object({ path: str(512), sha256: str(128) });

const BundleRef = z.object({
  id: label,
  kind: str(32),
  banner: str(512),
  bundle_digest: str(128),
  exported_at: optStr(64),
  files: z.array(BundleFile),
  href,
});

export const GitOpsHandoffPreviewData = z.object({
  bundle: BundleRef.nullable(),
  package_status: optStr(32),
  note: text,
});

export const DATA_CONTRACTS = {
  ControlSummary: ControlSummaryData,
  ControlCoverageMatrix: ControlCoverageMatrixData,
  ImpactAssessment: ImpactAssessmentData,
  ResourceImpactTable: ResourceImpactTableData,
  ApplicationReadinessPanel: ApplicationReadinessPanelData,
  PolicyDiffViewer: PolicyDiffViewerData,
  ScopeSelector: ScopeSelectorData,
  ExceptionReview: ExceptionReviewData,
  RolloutTimeline: RolloutTimelineData,
  ApprovalStatus: ApprovalStatusData,
  EvidencePanel: EvidencePanelData,
  GitOpsHandoffPreview: GitOpsHandoffPreviewData,
} as const;

export type DomainComponentName = keyof typeof DATA_CONTRACTS;
export type DataOf<N extends DomainComponentName> = z.infer<(typeof DATA_CONTRACTS)[N]>;

export const DOMAIN_COMPONENT_NAMES = Object.keys(DATA_CONTRACTS) as DomainComponentName[];
export const LAYOUT_COMPONENT_NAMES = ["CanvasStack", "CanvasNotice"] as const;
export type LayoutComponentName = (typeof LAYOUT_COMPONENT_NAMES)[number];
export type ComponentName = DomainComponentName | LayoutComponentName;
export const COMPONENT_NAMES: readonly ComponentName[] = [...DOMAIN_COMPONENT_NAMES, ...LAYOUT_COMPONENT_NAMES];

export function isDomainComponent(name: unknown): name is DomainComponentName {
  return typeof name === "string" && Object.prototype.hasOwnProperty.call(DATA_CONTRACTS, name);
}

/* ------------------------------------------------------------------ component props */

// Schemas handed to the A2UI renderer. DynamicValue lets the binder resolve {path} to the bound view.
export const DomainProps = z.object({ title: z.string().min(1).max(120), data: CommonSchemas.DynamicValue }).strict();
export const CanvasStackProps = z.object({ children: CommonSchemas.ChildList }).strict();
export const NoticeTone = z.enum(["info", "warning", "critical"]);
export const CanvasNoticeProps = z.object({ tone: NoticeTone, text: z.string().min(1).max(1000) }).strict();

// Stricter wire shapes checked before anything reaches the processor (mirrors the pydantic props models).
const DomainWireProps = z.object({
  title: z.string().min(1).max(120),
  data: z.object({ path: z.string().regex(DATA_PATH) }).strict(),
}).strict();
const CanvasStackWireProps = z.object({ children: z.array(z.string().regex(COMPONENT_ID)).min(1).max(40) }).strict();

export function wireProps(name: ComponentName): z.ZodTypeAny {
  if (name === "CanvasStack") return CanvasStackWireProps;
  if (name === "CanvasNotice") return CanvasNoticeProps;
  return DomainWireProps;
}

export function isCatalogComponent(name: unknown): name is ComponentName {
  return typeof name === "string" && (COMPONENT_NAMES as readonly string[]).includes(name);
}

/* ------------------------------------------------------------------ client actions */

const IdString = z.string().regex(ID);

export const CLIENT_ACTIONS = {
  "ws.select_scope": z.object({ scope_id: IdString }).strict(),
  "ws.run_assessment": z.object({ implementation_revision_id: IdString, target_scope_id: IdString }).strict(),
  "ws.prepare_exception_draft": z.object({ control_id: IdString, resource_id: z.string().regex(RESOURCE_ID) }).strict(),
} satisfies Record<CanvasActionName, z.ZodType<Record<string, string>>>;

export type ClientActionContext<N extends CanvasActionName> = z.infer<(typeof CLIENT_ACTIONS)[N]>;

export function isClientAction(name: unknown): name is CanvasActionName {
  return typeof name === "string" && Object.prototype.hasOwnProperty.call(CLIENT_ACTIONS, name);
}
