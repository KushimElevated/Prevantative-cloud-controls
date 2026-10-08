"use client";

import { Callout, Chip, defineDomainComponent, Muted, Panel, type DomainViewProps } from "./shared";

function ControlCoverageMatrixView({ data: d, panel }: DomainViewProps<"ControlCoverageMatrix">) {
  if (!d.available) {
    return (
      <Panel {...panel} links={d.links} subtitle={`Unit: ${d.unit}`}>
        <Callout tone="warning" title="Coverage is unknown">
          {d.unavailable_reason ?? "No completed assessment covers this scope."}
        </Callout>
        <Muted>Denominator: {d.denominator}</Muted>
      </Panel>
    );
  }
  const cell = (v: number | undefined) => (v === undefined ? "-" : v);
  return (
    <Panel {...panel} links={d.links} subtitle={`Unit: ${d.unit}`}
      meta={<>
        <Chip tone="emphasis">Verified protected {d.verified_protected_ratio}</Chip>
        {d.assessment_run_id && <Chip>Run {d.assessment_run_id}</Chip>}
      </>}>
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm" data-testid="ws-coverage-table">
          <caption className="sr-only">Coverage per scope</caption>
          <thead>
            <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
              <th scope="col" className="px-2 py-2 font-medium">Scope</th>
              {d.columns.map((c) => <th key={c.key} scope="col" className="px-2 py-2 text-right font-medium">{c.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={r.scope_id} className="border-b border-slate-100 dark:border-slate-800">
                <th scope="row" className="px-2 py-2 text-left font-medium text-slate-900 dark:text-slate-100">
                  {r.scope_name}
                  <div className="text-xs font-normal text-slate-500 dark:text-slate-400">{r.scope_id}{r.environment ? ` · ${r.environment}` : ""}</div>
                </th>
                {d.columns.map((c) => <td key={c.key} className="px-2 py-2 text-right tabular-nums">{cell(r.cells[c.key])}</td>)}
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="font-semibold text-slate-900 dark:text-slate-50">
              <th scope="row" className="px-2 py-2 text-left">Total</th>
              {d.columns.map((c) => <td key={c.key} className="px-2 py-2 text-right tabular-nums">{cell(d.totals[c.key])}</td>)}
            </tr>
          </tfoot>
        </table>
      </div>
      {d.rows.length === 0 && <Muted>No applicable resources in the scopes you can read.</Muted>}
      <Muted>Denominator: {d.denominator}</Muted>
    </Panel>
  );
}

export const ControlCoverageMatrix = defineDomainComponent("ControlCoverageMatrix", ControlCoverageMatrixView);
