"use client";
// Types and thin API calls for the optional AI Control Workspace (backend/app/api/routes_workspace.py).
// Every number and status shown in the workspace comes from these responses; nothing is recomputed here.

import { api } from "./api";

export type Experience = "classic" | "workspace";
export type WorkspaceMode = "deterministic" | "ai";

export type AiStatus = {
  available: boolean;
  reason: string | null;
  provider?: string | null;
  model?: string | null;
  refusal_fallbacks?: boolean;
};

export type Features = {
  a2ui_workspace: { enabled: boolean };
  workspace_ai: AiStatus;
};

export type Preferences = {
  experience: Experience;
  effective_experience: Experience;
  workspace_mode: WorkspaceMode;
};

export type FindingKind = "EVIDENCE" | "ESTIMATE" | "UNKNOWN" | "RECOMMENDATION";
export type Origin = "platform" | "ai";

export type Finding = {
  kind: FindingKind;
  text: string;
  origin: Origin;
  source: { type: string | null; id: string | null; href: string | null } | null;
};

export type Step = { tool: string; label: string; status: string; detail: string | null };

export type Summary = { text: string; origin: Origin; model?: string | null; label?: string | null };

export type Link = { label: string; href: string };

export type Action = {
  id: string;
  kind: "command" | "draft";
  label: string;
  enabled: boolean;
  reason: string | null;
  params: Record<string, unknown> | null;
  confirm: string | null;
  suggested_by_ai?: boolean;
};

export type NextDecision = { decision: string; owner_role: string; detail: string };

export type ContextPanelData = {
  control: { id: string; name: string; status: string; revision: number } | null;
  scope: { id: string; name: string; scope_type: string; environment: string | null; provider: string } | null;
  basis: { control_revision_id: string | null; assessment_run_id: string | null };
  next_decision: NextDecision | null;
  actions: Action[];
  links: Link[];
  data_labels: string[];
  identity: { display_name: string; roles: string[] };
};

export type Interpretation = {
  id: string;
  label: string;
  matched: string[];
  entities: Record<string, string | null>;
  notes: string[];
};

export type A2uiPayload = {
  protocol_version: string;
  catalog_id: string;
  surface_id: string;
  messages: unknown[];
};

export type Investigation = {
  investigation_id: string;
  question: string;
  generated_at: string;
  intent: Interpretation;
  steps: Step[];
  findings: Finding[];
  summary: Summary;
  context: ContextPanelData;
  a2ui: A2uiPayload;
  mode: WorkspaceMode;
  mode_note: string | null;
  requested_mode: WorkspaceMode;
};

export type InvestigationRequest = {
  question: string;
  mode: WorkspaceMode;
  intent?: string;
  control_id?: string;
  scope_id?: string;
};

export type IntentCatalog = {
  intents: { id: string; label: string; views: string[] }[];
  examples: string[];
};

export type DraftKind = "exception" | "rollout_plan" | "control";

export type Draft = {
  kind: DraftKind;
  title: string;
  basis: Record<string, unknown>;
  payload: Record<string, any>;
  required_fields: string[];
  can_submit: boolean;
  blocking_reasons: string[];
  preview: Record<string, any> | null;
  notes: string[];
  submit_via: string;
};

export type DraftSubmitResult = {
  created: { type: string; id: string; href: string };
  note: string;
};

// Mirror of SAFE_HREF in backend/app/workspace/catalog.py: internal routes only, never a scheme or host.
export const SAFE_HREF =
  /^\/(dashboard|controls|assessments|exceptions|rollouts|bundles|implementations|audit|workspace)(\/[A-Za-z0-9._~:@-]+)*(\?[A-Za-z0-9._~:@=&%+/-]*)?$/;

export function isSafeHref(href: unknown): href is string {
  return typeof href === "string" && href.length <= 512 && SAFE_HREF.test(href);
}

/** Drops null/undefined/empty values so optional tool parameters are omitted rather than sent as null. */
export function compact(params: Record<string, unknown> | null | undefined): Record<string, unknown> {
  return Object.fromEntries(Object.entries(params ?? {}).filter(([, v]) => v !== null && v !== undefined && v !== ""));
}

export const getFeatures = () => api<Features>("/features");
export const getPreferences = () => api<Preferences>("/me/preferences");
export const putPreferences = (body: { experience: Experience; workspace_mode: WorkspaceMode }) =>
  api<Preferences>("/me/preferences", { method: "PUT", body });

export const getIntents = () => api<IntentCatalog>("/workspace/intents");
export const runInvestigation = (body: InvestigationRequest) =>
  api<Investigation>("/workspace/investigations", { method: "POST", body: compact(body) });

export const prepareDraft = (kind: DraftKind, params: Record<string, unknown>) =>
  api<Draft>("/workspace/drafts/prepare", { method: "POST", body: { kind, params: compact(params) } });
export const submitDraft = (draft: Draft, payload: Record<string, unknown>) =>
  api<DraftSubmitResult>("/workspace/drafts/submit", {
    method: "POST",
    body: { kind: draft.kind, basis: draft.basis, payload, confirmed: true },
  });

/** The existing Classic assessment endpoint; the workspace adds no command endpoints of its own. */
export const runAssessment = (body: { implementation_revision_id: string; target_scope_id: string }) =>
  api<{ id: string; status: string }>("/assessments", { method: "POST", body });
