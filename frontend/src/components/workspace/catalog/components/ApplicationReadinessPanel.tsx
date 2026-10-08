"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize } from "@/lib/format";
import { Callout, Chip, defineDomainComponent, indexed, Muted, Panel, ProvenanceChip, SubHeading, type DomainViewProps } from "./shared";

function ApplicationReadinessPanelView({ data: d, panel }: DomainViewProps<"ApplicationReadinessPanel">) {
  return (
    <Panel {...panel} subtitle={d.run_id ? `From run ${d.run_id}` : undefined}>
      {!d.available && <Callout tone="warning" title="Readiness per resource is UNKNOWN">{d.note}</Callout>}
      {d.available && (
        <div>
          <SubHeading hint={`(${d.applications.length})`}>Applications</SubHeading>
          {d.applications.length === 0 ? <Muted>No evaluated applications in this scope.</Muted> : (
            <ul className="space-y-3">
              {d.applications.map((a) => (
                <li key={a.application} className="rounded-md border border-slate-200 dark:border-slate-700" data-testid={`ws-app-${a.application}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                    <span className="font-medium">{a.application}</span>
                    <span className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                      {a.resources} resource{a.resources === 1 ? "" : "s"}
                      <StatusBadge value={a.readiness} />
                    </span>
                  </div>
                  {a.blockers.length > 0 && (
                    <div className="border-t border-slate-100 px-3 pb-2 dark:border-slate-800">
                      <DataTable rows={indexed(a.blockers)} rowKey={({ i }) => String(i)} caption={`Blockers for ${a.application}`} columns={[
                        { key: "k", header: "Blocker", render: ({ r }) => <span className="font-medium">{humanize(r.kind)}</span> },
                        { key: "m", header: "Why", render: ({ r }) => r.message },
                        { key: "r", header: "Resolution", render: ({ r }) => r.resolution },
                        { key: "o", header: "Owner", render: ({ r }) => <span>{r.owner_team}{r.scope_id && <div className="text-xs text-slate-500">{r.scope_id}</div>}</span> },
                      ]} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div>
        <SubHeading hint="(attested by the named team, not tested)">Supplied evidence</SubHeading>
        <DataTable rows={d.evidence} rowKey={(e) => e.id} caption="Readiness evidence" empty="No readiness evidence supplied for this scope." columns={[
          { key: "a", header: "Application", render: (e) => <span>{e.application}<div className="text-xs text-slate-500 dark:text-slate-400">{e.scope_id}</div></span> },
          { key: "p", header: "Prerequisite", render: (e) => humanize(e.prerequisite) },
          { key: "s", header: "Status", render: (e) => <StatusBadge value={e.status} /> },
          { key: "c", header: "Collected", render: (e) => (
            <span>{fmtTime(e.collected_at)}{e.stale && <div className="mt-0.5"><Chip tone="warning" icon="!">Stale: does not count</Chip></div>}</span>
          ) },
          { key: "b", header: "Provided by", render: (e) => e.provided_by },
          { key: "v", header: "Provenance", render: (e) => <ProvenanceChip value={e.provenance} /> },
        ]} />
      </div>
      {d.available && <Muted>{d.note}</Muted>}
    </Panel>
  );
}

export const ApplicationReadinessPanel = defineDomainComponent("ApplicationReadinessPanel", ApplicationReadinessPanelView);
