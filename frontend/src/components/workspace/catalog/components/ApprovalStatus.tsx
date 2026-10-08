"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize, shortDigest } from "@/lib/format";
import { SafeLink } from "../SafeLink";
import { Callout, Chip, defineDomainComponent, Facts, indexed, Mono, Muted, Panel, SubHeading, type DomainViewProps } from "./shared";

function ApprovalStatusView({ data: d, panel }: DomainViewProps<"ApprovalStatus">) {
  const pkg = d.package;
  return (
    <Panel {...panel} meta={<><Chip icon="◇">Read-only</Chip>{pkg && <StatusBadge value={pkg.status} />}</>}>
      <Callout tone="info" title="Approvals happen in the Classic Experience" testId="ws-approval-classic">
        Approve or reject change packages in the Classic Experience. The workspace shows decisions but never records one.
      </Callout>
      {pkg ? (
        <Facts items={[
          ["Change package", <SafeLink key="p" href={pkg.href}>{pkg.id}</SafeLink>],
          ["Revision", `r${pkg.revision}`],
          ["Status", <StatusBadge key="s" value={pkg.status} />],
          ["Through stage", humanize(pkg.through_stage)],
          ["Manifest digest", <Mono key="d" title={pkg.manifest_digest}>{shortDigest(pkg.manifest_digest)}</Mono>],
        ]} />
      ) : <Muted>No change package exists yet.</Muted>}
      <div>
        <SubHeading>Required roles</SubHeading>
        <div className="flex flex-wrap gap-1.5">
          {d.required_roles.length ? d.required_roles.map((r) => <Chip key={r}>{humanize(r)}</Chip>) : <Muted>None recorded.</Muted>}
        </div>
      </div>
      <div>
        <SubHeading hint={`(${d.decisions.length})`}>Decisions</SubHeading>
        <DataTable rows={indexed(d.decisions)} rowKey={({ i }) => String(i)} caption="Decisions" empty="No decisions recorded." columns={[
          { key: "role", header: "Role", render: ({ r }) => humanize(r.role) },
          { key: "actor", header: "Actor", render: ({ r }) => r.actor_id },
          { key: "dec", header: "Decision", render: ({ r }) => <StatusBadge value={r.decision} /> },
          { key: "at", header: "Decided", render: ({ r }) => fmtTime(r.decided_at) },
          { key: "bound", header: "Current digest", render: ({ r }) => r.bound_to_current_digest
            ? <span><span aria-hidden="true" className="text-status-good">✓ </span>Bound</span>
            : <span><span aria-hidden="true" className="text-status-serious">▲ </span>Not bound (does not count)</span> },
        ]} />
      </div>
      {(pkg || d.failing_gates.length > 0) && (
        <div>
          <SubHeading hint="(as recorded at the last evaluation)">Failing gates</SubHeading>
          <DataTable rows={d.failing_gates} rowKey={(g) => g.id} caption="Failing gates" empty="No failing gates recorded." columns={[
            { key: "g", header: "Gate", render: (g) => <span className="font-medium">{g.label}<div className="text-xs font-normal text-slate-500">{g.id}</div></span> },
            { key: "p", header: "Phase", render: (g) => humanize(g.phase) },
            { key: "d", header: "Detail", render: (g) => <span className="break-all font-mono text-xs">{g.detail}</span> },
          ]} />
        </div>
      )}
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const ApprovalStatus = defineDomainComponent("ApprovalStatus", ApprovalStatusView);
