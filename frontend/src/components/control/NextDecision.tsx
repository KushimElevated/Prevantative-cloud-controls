"use client";

import { humanize } from "@/lib/format";

export function NextDecision({ detail }: { detail: any }) {
  const next = detail.next_decision;
  return (
    <div className="mb-6 rounded-lg border-2 border-sky-300 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950" data-testid="next-decision">
      <div className="text-xs font-semibold uppercase tracking-wide text-sky-800 dark:text-sky-200">Next required decision</div>
      <div className="mt-1 text-lg font-semibold text-slate-900 dark:text-slate-50">{next.decision}</div>
      <div className="text-sm text-slate-700 dark:text-slate-200">Owner: {humanize(next.owner_role)} · {next.detail}</div>
      {detail.pending_decisions.length > 1 && (
        <details className="mt-2 text-sm">
          <summary className="cursor-pointer text-sky-800 dark:text-sky-200">{detail.pending_decisions.length - 1} more pending</summary>
          <ul className="mt-1 list-disc pl-5">
            {detail.pending_decisions.slice(1).map((d: any) => <li key={d.decision}>{d.decision} <span className="text-slate-500">({humanize(d.owner_role)})</span></li>)}
          </ul>
        </details>
      )}
    </div>
  );
}
