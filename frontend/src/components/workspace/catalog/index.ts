import { Catalog } from "@a2ui/web_core/v0_9";
import type { ReactComponentImplementation } from "@a2ui/react/v0_9";
import { CATALOG_ID, type ComponentName } from "./contracts";
import { ApplicationReadinessPanel } from "./components/ApplicationReadinessPanel";
import { ApprovalStatus } from "./components/ApprovalStatus";
import { ControlCoverageMatrix } from "./components/ControlCoverageMatrix";
import { ControlSummary } from "./components/ControlSummary";
import { EvidencePanel } from "./components/EvidencePanel";
import { ExceptionReview } from "./components/ExceptionReview";
import { GitOpsHandoffPreview } from "./components/GitOpsHandoffPreview";
import { ImpactAssessment } from "./components/ImpactAssessment";
import { CanvasNotice, CanvasStack } from "./components/layout";
import { PolicyDiffViewer } from "./components/PolicyDiffViewer";
import { ResourceImpactTable } from "./components/ResourceImpactTable";
import { RolloutTimeline } from "./components/RolloutTimeline";
import { ScopeSelector } from "./components/ScopeSelector";

export const COMPONENT_IMPLEMENTATIONS: Record<ComponentName, ReactComponentImplementation> = {
  ControlSummary,
  ControlCoverageMatrix,
  ImpactAssessment,
  ResourceImpactTable,
  ApplicationReadinessPanel,
  PolicyDiffViewer,
  ScopeSelector,
  ExceptionReview,
  RolloutTimeline,
  ApprovalStatus,
  EvidencePanel,
  GitOpsHandoffPreview,
  CanvasStack,
  CanvasNotice,
};

/** A fresh Catalog instance (cheap; the implementations themselves are shared). */
export function createWorkspaceCatalog(): Catalog<ReactComponentImplementation> {
  return new Catalog(CATALOG_ID, Object.values(COMPONENT_IMPLEMENTATIONS));
}

export { CATALOG_ID, CLIENT_ACTIONS, COMPONENT_NAMES, DATA_CONTRACTS, PROTOCOL_VERSION, SAFE_HREF } from "./contracts";
export { validateServerMessages, type ValidationResult } from "./validate";
export { SafeLink, isSafeHref } from "./SafeLink";
