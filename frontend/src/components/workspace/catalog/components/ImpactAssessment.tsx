"use client";

import { Button, StatusBadge } from "@/components/ui";
import { fmtTime } from "@/lib/format";
import {
  BulletList,
  Callout,
  Chip,
  ConfidenceList,
  CountTiles,
  defineDomainComponent,
  Facts,
  Mono,
  Muted,
  Panel,
  ProvenanceChip,
  SubHeading,
  type DomainViewProps,
  type Emit,
} from "./shared";

type Data = DomainViewProps<"ImpactAssessment">["data"];

function RunControl({ d, emit, label, variant }: { d: Data; emit: Emit; label: string; variant: "primary" | "secondary" }) {
  if (d.can_run && d.run_action) {
    const action = d.run_action;
    return (
      <div className="flex flex-wrap items-center gap-3">
        <Button variant={variant} testId="ws-run-assessment"
                onClick={() => emit("ws.run_assessment", {
                  implementation_revision_id: action.implementation_revision_id,
                  target_scope_id: action.target_scope_id,
                })}>
          {label}
        </Button>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          Target {action.target_scope_id}. You confirm before anything runs; results are persisted and audited.
        </span>
      </div>
    );
  }
  if (d.run_blocked_reason) {
    return <Muted><span aria-hidden="true">– </span>Cannot run an assessment here: {d.run_blocked_reason}</Muted>;
  }
  return null;
}

function Reconciliation({ d }: { d: Data }) {
  if (d.reconciles === true) {
    return <span className="text-xs text-slate-600 dark:text-slate-300"><span aria-hidden="true" className="text-status-good">✓ </span>Counts reconcile with the {d.total_resources ?? "-"} resources evaluated (mutually exclusive).</span>;
  }
  if (d.reconciles === false) {
    return <span className="text-xs font-medium text-red-800 dark:text-red-300"><span aria-hidden="true">▲ </span>Counts do not reconcile with the {d.total_resources ?? "-"} resources evaluated.</span>;
  }
  return <span className="text-xs text-slate-500">Reconciliation was not reported.</span>;
}

function RequestImpact({ d }: { d: Data }) {
  const ri = d.request_impact;
  if (!ri) {
    return <Callout tone="warning" title="Request impact: UNKNOWN">No request impact was reported for this scope.</Callout>;
  }
  if (!ri.evidence_present || !ri.counts) {
    return (
      <Callout tone="warning" title="Potentially blocked operations: UNKNOWN (not zero)" testId="ws-request-unknown">
        {ri.note}
      </Callout>
    );
  }
  return (
    <div className="space-y-2">
      <CountTiles counts={ri.counts} testId="ws-request-counts" caption="Request impact (estimate)" />
      <Muted>Estimate from {ri.total} supplied representative request{ri.total === 1 ? "" : "s"}. {ri.note}</Muted>
    </div>
  );
}

function ImpactAssessmentView({ data: d, panel, emit }: DomainViewProps<"ImpactAssessment">) {
  if (!d.available) {
    return (
      <Panel {...panel} links={d.links} meta={<StatusBadge value="UNKNOWN" label="Impact unknown" />}
        subtitle={d.requested_scope_id ? `Requested scope ${d.requested_scope_id}` : undefined}>
        <Callout tone="warning" title="No persisted impact assessment" testId="ws-impact-unavailable">
          {d.unavailable_reason ?? "Impact is UNKNOWN until an assessment runs."}
        </Callout>
        <RunControl d={d} emit={emit} label="Run impact assessment…" variant="primary" />
      </Panel>
    );
  }
  return (
    <Panel {...panel} links={d.links}
      subtitle={<>Persisted run <Mono>{d.run_id ?? "-"}</Mono></>}
      meta={<>
        <StatusBadge value={d.status} />
        <ProvenanceChip value={d.data_provenance} />
        {d.basis_current === true && <Chip icon="✓">Basis current</Chip>}
        {d.basis_current === false && <Chip tone="warning" icon="!">Basis outdated</Chip>}
      </>}>
      <Facts items={[
        ["Requested scope", d.requested_scope_id ?? "All readable scopes"],
        ["Run target scope", d.target_scope_id ?? "-"],
        ["Assessed", fmtTime(d.assessed_at)],
        ["Evaluator", d.evaluator ?? "-"],
      ]} />
      {d.filtered_to_scope && d.filter_note && <Callout tone="info" title="Filtered view" testId="ws-filter-note">{d.filter_note}</Callout>}
      {d.basis_notes.length > 0 && (
        <Callout tone="warning" title="The assessment basis has changed since this run" testId="ws-basis-notes">
          <BulletList items={d.basis_notes} />
        </Callout>
      )}

      <section aria-label="Current configuration">
        <SubHeading hint="(resources; one result per resource)">1. Current configuration</SubHeading>
        {d.configuration ? <CountTiles counts={d.configuration} testId="ws-config-counts" caption="Configuration results" /> : <Muted>Not reported for this scope.</Muted>}
        <div className="mt-1.5"><Reconciliation d={d} /></div>
      </section>

      <section aria-label="Exception dispositions">
        <SubHeading hint="(reported separately; an exception never makes a resource compliant)">Exception dispositions</SubHeading>
        {d.exceptions ? (
          <div className="flex flex-wrap gap-x-4 gap-y-1.5" data-testid="ws-exception-counts">
            {Object.entries(d.exceptions).map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-1"><StatusBadge value={k} /><span className="font-semibold tabular-nums">{v}</span></span>
            ))}
          </div>
        ) : <Muted>Not reported for this scope.</Muted>}
      </section>

      <section aria-label="Request impact">
        <SubHeading hint="(estimate from representative requests only)">2. Request impact</SubHeading>
        <RequestImpact d={d} />
      </section>

      <section aria-label="Operational readiness">
        <SubHeading hint="(resources by readiness)">3. Operational readiness</SubHeading>
        {d.readiness ? <CountTiles counts={d.readiness} testId="ws-readiness-counts" caption="Readiness" /> : <Muted>Not reported for this scope.</Muted>}
      </section>

      {d.newly_preventive_resources !== null && d.newly_preventive_resources !== undefined && (
        <p className="text-sm" data-testid="ws-newly-preventive">
          <span className="text-lg font-semibold tabular-nums">{d.newly_preventive_resources}</span>{" "}
          resource{d.newly_preventive_resources === 1 ? "" : "s"} would newly receive preventive coverage.
        </p>
      )}

      <section aria-label="Confidence">
        <SubHeading>Confidence</SubHeading>
        <ConfidenceList confidence={d.confidence} />
      </section>
      {d.disclosure && <Muted>{d.disclosure}</Muted>}
      {d.basis_current === false && <RunControl d={d} emit={emit} label="Run a fresh impact assessment…" variant="secondary" />}
    </Panel>
  );
}

export const ImpactAssessment = defineDomainComponent("ImpactAssessment", ImpactAssessmentView);
