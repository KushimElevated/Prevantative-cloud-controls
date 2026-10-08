"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize, shortDigest } from "@/lib/format";
import {
  BulletList,
  Callout,
  Chip,
  ConfidenceList,
  defineDomainComponent,
  Mono,
  Muted,
  Panel,
  ProvenanceChip,
  SubHeading,
  type DomainViewProps,
} from "./shared";

function freshness(stale: boolean | null | undefined) {
  if (stale === true) return <StatusBadge value="STALE" label="Stale" />;
  if (stale === false) return <span><span aria-hidden="true" className="text-status-good">✓ </span>Fresh</span>;
  return <span className="text-slate-500">Not assessed</span>;
}

function EvidencePanelView({ data: d, panel }: DomainViewProps<"EvidencePanel">) {
  return (
    <Panel {...panel}
      meta={d.demo_data
        ? <Chip tone="warning" icon="◆" testId="ws-demo-data">Demo data</Chip>
        : <Chip icon="✓">Live sources</Chip>}>
      {d.demo_data && (
        <Callout tone="warning" title="Demo data">
          These results come from fixture or mock sources, not from live cloud state. Treat counts as illustrative.
        </Callout>
      )}
      <div>
        <SubHeading hint={`(${d.sources.length})`}>Sources</SubHeading>
        <DataTable rows={d.sources} rowKey={(s) => `${s.kind}:${s.id}`} caption="Evidence sources" empty="No sources recorded for this scope." columns={[
          { key: "k", header: "Source", render: (s) => <span><span className="font-medium">{s.label}</span><div className="text-xs text-slate-500 dark:text-slate-400">{humanize(s.kind)} · {s.id}</div></span> },
          { key: "p", header: "Provenance", render: (s) => <ProvenanceChip value={s.provenance} /> },
          { key: "c", header: "Collected", render: (s) => fmtTime(s.collected_at) },
          { key: "f", header: "Freshness", render: (s) => freshness(s.stale) },
          { key: "d", header: "Digest", render: (s) => s.digest ? <Mono title={s.digest}>{shortDigest(s.digest)}</Mono> : "-" },
        ]} />
      </div>
      <div>
        <SubHeading>Confidence</SubHeading>
        <ConfidenceList confidence={d.confidence} />
      </div>
      {d.disclosure && <Muted>{d.disclosure}</Muted>}
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <SubHeading>Limitations</SubHeading>
          <BulletList items={d.limitations} />
        </div>
        <div>
          <SubHeading>Assumptions</SubHeading>
          <BulletList items={d.assumptions} />
        </div>
      </div>
    </Panel>
  );
}

export const EvidencePanel = defineDomainComponent("EvidencePanel", EvidencePanelView);
