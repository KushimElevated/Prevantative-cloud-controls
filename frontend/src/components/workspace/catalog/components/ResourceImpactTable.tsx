"use client";

import { useState } from "react";
import { Button, DataTable, Select, StatusBadge } from "@/components/ui";
import { humanize, leaf } from "@/lib/format";
import { Chip, defineDomainComponent, Muted, Panel, type DomainViewProps } from "./shared";

const ALL = "ALL";
const KNOWN_RESULTS = ["COMPLIANT", "NON_COMPLIANT", "UNKNOWN", "NOT_APPLICABLE"];

function Reasons({ reasons }: { reasons: string[] }) {
  if (!reasons.length) return <span className="text-slate-500">-</span>;
  const [first, ...rest] = reasons;
  return (
    <div className="max-w-md text-xs text-slate-700 dark:text-slate-200">
      {first}
      {rest.length > 0 && (
        <details className="mt-0.5">
          <summary className="cursor-pointer text-sky-700 dark:text-sky-400">{rest.length} more</summary>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">{rest.map((r, i) => <li key={i}>{r}</li>)}</ul>
        </details>
      )}
    </div>
  );
}

function ResourceImpactTableView({ data: d, panel, emit }: DomainViewProps<"ResourceImpactTable">) {
  const [filter, setFilter] = useState(ALL);
  if (!d.available) {
    return (
      <Panel {...panel} links={d.links}>
        <Muted>No persisted assessment covers this scope, so per-resource results are unknown.</Muted>
      </Panel>
    );
  }
  const present = new Set(d.rows.map((r) => r.configuration_result).filter((v): v is string => !!v));
  const values = [...KNOWN_RESULTS.filter((v) => present.has(v)), ...Array.from(present).filter((v) => !KNOWN_RESULTS.includes(v))];
  const active = filter === ALL || present.has(filter) ? filter : ALL;
  const rows = active === ALL ? d.rows : d.rows.filter((r) => r.configuration_result === active);
  return (
    <Panel {...panel} links={d.links}
      subtitle={<>Run {d.run_id ?? "-"}{d.scope_id ? ` · scope ${d.scope_id}` : ""} · {d.total} resource{d.total === 1 ? "" : "s"}</>}
      meta={d.truncated ? <Chip tone="warning" icon="!">Showing first {d.rows.length} of {d.total}</Chip> : undefined}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <Select label="Configuration result" value={active} onChange={setFilter} testId="ws-resource-filter"
                options={[{ value: ALL, label: "All results" }, ...values.map((v) => ({ value: v, label: humanize(v) }))]} />
        {active !== ALL && <span className="text-xs text-slate-500 dark:text-slate-400">Showing {rows.length} of {d.rows.length} listed rows</span>}
      </div>
      <DataTable rows={rows} rowKey={(r) => r.resource_id} caption="Per-resource results" empty="No resources match this filter." columns={[
        { key: "res", header: "Resource", render: (r) => (
          <span>
            <span className="font-medium">{r.name}</span>
            <div className="text-xs text-slate-500 dark:text-slate-400" title={r.resource_id}>{r.resource_type ?? leaf(r.resource_id)}</div>
            {r.scope_id && <div className="text-xs text-slate-500 dark:text-slate-400">{r.scope_id}</div>}
          </span>
        ) },
        { key: "app", header: "Application / owner", render: (r) => (
          <span>{r.application ?? "-"}{r.owner && <div className="text-xs text-slate-500 dark:text-slate-400">{r.owner}</div>}</span>
        ) },
        { key: "cfg", header: "Configuration", render: (r) => (
          <span className="flex flex-col items-start gap-1">
            <StatusBadge value={r.configuration_result} />
            {r.applicability && r.applicability !== "APPLICABLE" && r.applicability !== r.configuration_result && <StatusBadge value={r.applicability} />}
          </span>
        ) },
        { key: "exc", header: "Exception", render: (r) => (
          <span>
            <StatusBadge value={r.exception_disposition} />
            {r.exception_id && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{r.exception_id}</div>}
          </span>
        ) },
        { key: "rdy", header: "Readiness", render: (r) => <StatusBadge value={r.readiness} label={r.readiness ? undefined : "Not evaluated"} /> },
        { key: "why", header: "Why", render: (r) => (
          <div>
            <Reasons reasons={r.reasons} />
            {r.missing_fields.length > 0 && <div className="mt-1 text-xs text-amber-800 dark:text-amber-300"><span aria-hidden="true">! </span>Missing: {r.missing_fields.join(", ")}</div>}
          </div>
        ) },
        { key: "act", header: "Action", render: (r) => r.can_prepare_exception ? (
          <Button variant="secondary" testId={`ws-prepare-exception-${r.name}`}
                  onClick={() => emit("ws.prepare_exception_draft", { control_id: d.control_id, resource_id: r.resource_id })}>
            Prepare exception draft<span className="sr-only"> for {r.name}</span>
          </Button>
        ) : <span className="text-slate-400">-</span> },
      ]} />
      <Muted>Results are the persisted output of the assessment run; preparing a draft saves nothing until you review and submit it.</Muted>
    </Panel>
  );
}

export const ResourceImpactTable = defineDomainComponent("ResourceImpactTable", ResourceImpactTableView);
