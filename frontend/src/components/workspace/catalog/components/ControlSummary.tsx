"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { humanize, leaf } from "@/lib/format";
import { BulletList, Callout, Chip, defineDomainComponent, Facts, Mono, Panel, SubHeading, yesNo, type DomainViewProps } from "./shared";

function ControlSummaryView({ data: d, panel }: DomainViewProps<"ControlSummary">) {
  return (
    <Panel {...panel} links={d.links}
      subtitle={<><Mono>{d.control_id}</Mono> <span className="ml-1">r{d.revision} · {d.name}</span></>}
      meta={<>
        <StatusBadge value={d.status} />
        <Chip>Severity: {humanize(d.severity)}</Chip>
        <Chip>{humanize(d.origin)}</Chip>
      </>}>
      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <SubHeading>Security objective</SubHeading>
          <p className="leading-relaxed">{d.security_objective}</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-950">
          <SubHeading hint="(what it prevents, and what it does not)">Prevention boundary</SubHeading>
          <p className="leading-relaxed">{d.prevention_boundary}</p>
        </div>
      </div>
      <Facts items={[
        ["Providers", d.providers.map(humanize).join(", ") || "-"],
        ["Resource types", d.resource_types.length ? <span className="flex flex-wrap gap-1">{d.resource_types.map((t) => <Mono key={t}>{t}</Mono>)}</span> : "-"],
        ["Exception eligible", yesNo(d.exception_eligible)],
        ["Operational owner", d.operational_owner],
        ["Security owner", d.security_owner],
        ["Engineering owner", d.engineering_owner],
      ]} />
      <div>
        <SubHeading>Limitations</SubHeading>
        <BulletList items={d.limitations} empty="No limitations recorded." />
      </div>
      <div>
        <SubHeading hint={`(${d.implementations.length})`}>Implementations</SubHeading>
        <DataTable rows={d.implementations} rowKey={(i) => i.id} empty="No implementations yet." caption="Implementations" columns={[
          { key: "name", header: "Implementation", render: (i) => <span>{i.name}<div className="text-xs text-slate-500 dark:text-slate-400">{humanize(i.provider)} · {humanize(i.mechanism_role)}</div></span> },
          { key: "rev", header: "Revision", render: (i) => <span className="inline-flex items-center gap-1.5">r{i.latest_revision} <StatusBadge value={i.status} /></span> },
          { key: "val", header: "Validation", render: (i) => i.validation_outcome ? <StatusBadge value={i.validation_outcome} /> : <span className="text-slate-500">Not validated</span> },
          { key: "int", header: "Integration ready", render: (i) => yesNo(i.integration_ready) },
          { key: "ver", header: "Verification", render: (i) => <StatusBadge value={i.verification_status} /> },
          { key: "src", header: "Source", render: (i) => <span><Mono title={i.source_ref}>{leaf(i.source_ref)}</Mono>{i.pinned_version && <div className="text-xs text-slate-500">pinned {i.pinned_version}</div>}</span> },
        ]} />
      </div>
      {d.next_decision ? (
        <Callout tone="info" title={<>Next decision: {d.next_decision.decision}</>} testId="ws-next-decision">
          <div>Owner: {humanize(d.next_decision.owner_role)}</div>
          {d.next_decision.detail && <div className="mt-0.5 text-xs opacity-80">{d.next_decision.detail}</div>}
        </Callout>
      ) : <p className="text-sm text-slate-500 dark:text-slate-400">No pending decision recorded.</p>}
    </Panel>
  );
}

export const ControlSummary = defineDomainComponent("ControlSummary", ControlSummaryView);
