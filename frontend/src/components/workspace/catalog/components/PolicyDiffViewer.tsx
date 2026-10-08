"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize, leaf, shortDigest } from "@/lib/format";
import { defineDomainComponent, Facts, indexed, Mono, Muted, Panel, ProvenanceChip, SubHeading, type DomainViewProps } from "./shared";

function Json({ value, label }: { value: unknown; label: string }) {
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-slate-500 dark:text-slate-400">{label}</div>
      <pre aria-label={label} className="max-h-60 overflow-auto rounded bg-slate-50 p-2 font-mono text-xs text-slate-800 dark:bg-slate-950 dark:text-slate-200">
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function PolicyDiffViewerView({ data: d, panel }: DomainViewProps<"PolicyDiffViewer">) {
  const p = d.proposed;
  return (
    <Panel {...panel} subtitle="Recorded native bindings (baseline) versus the latest implementation revision (proposed)">
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-md border border-slate-200 p-3 dark:border-slate-700" data-testid="ws-diff-baseline">
          <SubHeading hint={`(${d.baseline.length} binding${d.baseline.length === 1 ? "" : "s"})`}>Baseline</SubHeading>
          {d.baseline.length === 0 ? <Muted>No native bindings are recorded for this scope.</Muted> : (
            <ul className="space-y-3">
              {d.baseline.map((b) => (
                <li key={b.binding_id} className="space-y-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Mono title={b.native_id}>{leaf(b.native_id)}</Mono>
                    <ProvenanceChip value={b.provenance} />
                  </div>
                  <Facts items={[
                    ["Scope", b.scope_id],
                    ["Effect", b.effect ?? "Not recorded"],
                    ["Enforcement mode", b.enforcement_mode ?? "Not recorded"],
                    ["Management", humanize(b.management)],
                    ["Origin", humanize(b.origin)],
                    ["Observed", fmtTime(b.observed_at)],
                  ]} />
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="rounded-md border border-sky-200 p-3 dark:border-sky-900" data-testid="ws-diff-proposed">
          <SubHeading>Proposed</SubHeading>
          {!p ? <Muted>No implementation revision exists for this provider.</Muted> : (
            <div className="space-y-3">
              <Facts items={[
                ["Revision", <Mono key="r">{p.implementation_revision_id}</Mono>],
                ["Policy kind", humanize(p.policy_kind)],
                ["Source", <Mono key="s" title={p.source_ref}>{leaf(p.source_ref)}</Mono>],
                ["Pinned version", p.pinned_version ?? "Not pinned"],
                ["Content digest", <Mono key="d" title={p.content_digest ?? undefined}>{shortDigest(p.content_digest)}</Mono>],
                ["Verification", p.verification],
              ]} />
              <Json label="Parameters" value={p.parameters} />
              <Json label="Assignment settings" value={p.assignment_settings} />
            </div>
          )}
        </div>
      </div>
      <div>
        <SubHeading hint={`(${d.changes.length})`}>Changes</SubHeading>
        <DataTable rows={indexed(d.changes)} rowKey={({ i }) => String(i)} empty="No differences recorded between baseline and proposal." caption="Changes" columns={[
          { key: "f", header: "Field", render: ({ r }) => <Mono>{r.field}</Mono> },
          { key: "s", header: "Scope", render: ({ r }) => r.scope_id ?? "-" },
          { key: "b", header: "Baseline", render: ({ r }) => r.baseline },
          { key: "p", header: "Proposed", render: ({ r }) => <span className="font-medium"><span aria-hidden="true">→ </span>{r.proposed}</span> },
        ]} />
      </div>
      <div>
        <SubHeading hint="(applicable resources by existing coverage)">Baseline coverage</SubHeading>
        {Object.keys(d.baseline_coverage_counts).length === 0 ? <Muted>No persisted assessment results in this scope.</Muted> : (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(d.baseline_coverage_counts).map(([k, v]) => (
              <span key={k} className="inline-flex items-center gap-1"><StatusBadge value={k} /><span className="font-semibold tabular-nums">{v}</span></span>
            ))}
          </div>
        )}
      </div>
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const PolicyDiffViewer = defineDomainComponent("PolicyDiffViewer", PolicyDiffViewerView);
