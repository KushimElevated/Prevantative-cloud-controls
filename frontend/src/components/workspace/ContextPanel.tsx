"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { humanize } from "@/lib/format";
import { Empty, StatusBadge } from "@/components/ui";
import { Action, ContextPanelData, isSafeHref } from "@/lib/workspace";
import { Panel, PanelHeading } from "./Panel";

/** Stacked label/value pairs: the context column is too narrow for the Classic two-column KeyValue. */
function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="space-y-1.5 text-sm">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt className="text-xs font-medium text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="break-words text-slate-900 dark:text-slate-100">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function ActionItem({ action, onAction, busy }: { action: Action; onAction: (a: Action) => void; busy: boolean }) {
  const reasonId = `ws-action-reason-${action.id}`;
  return (
    <li className="rounded border border-slate-200 p-2 dark:border-slate-700">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" data-testid={`ws-action-${action.id}`} disabled={!action.enabled || busy}
                aria-describedby={!action.enabled && action.reason ? reasonId : undefined}
                onClick={() => onAction(action)}
                className={`rounded px-3 py-1.5 text-left text-sm font-medium focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed ${action.kind === "command"
                  ? "bg-sky-700 text-white hover:bg-sky-800 disabled:bg-slate-300"
                  : "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100"}`}>
          {action.label}
        </button>
        {action.suggested_by_ai && (
          <span className="rounded border border-violet-300 bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-100">
            <span aria-hidden="true">✦ </span>Suggested by AI
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        {action.kind === "command"
          ? "Governed command: you will see exactly what happens and confirm first."
          : "Draft only: nothing is saved until you review and submit it."}
      </p>
      {!action.enabled && action.reason && (
        <p id={reasonId} className="mt-1 text-xs text-amber-800 dark:text-amber-200">
          <span aria-hidden="true">! </span>{action.reason}
        </p>
      )}
    </li>
  );
}

export function ContextPanel({ context, onAction, busy = false, status, children }: {
  context: ContextPanelData | null; onAction: (a: Action) => void; busy?: boolean;
  /** Outcome of the last command, shown above the actions. */
  status?: ReactNode;
  /** The current draft, if any. */
  children?: ReactNode;
}) {
  return (
    <Panel title="Context & actions" testId="ws-context"
           description="What the canvas is about, who you are, and what you can do next.">
      {!context ? (
        <>
          <Empty message="No investigation yet." />
          {children}
        </>
      ) : (
        <div>
          <PanelHeading>Current context</PanelHeading>
          <Facts items={[
            ["Control", context.control
              ? <span><code>{context.control.id}</code> r{context.control.revision} <StatusBadge value={context.control.status} /></span>
              : "None matched"],
            ["Scope", context.scope ? <span><code>{context.scope.id}</code> <span className="text-slate-500">{context.scope.name}</span></span> : "-"],
            ["Control revision", context.basis.control_revision_id ? <code>{context.basis.control_revision_id}</code> : "-"],
            ["Assessment", context.basis.assessment_run_id ? <code>{context.basis.assessment_run_id}</code> : "None covering this scope"],
          ]} />

          {context.next_decision && (
            <>
              <PanelHeading>Next required decision</PanelHeading>
              <div data-testid="ws-next-decision" className="rounded border border-sky-300 bg-sky-50 p-2 text-sm dark:border-sky-800 dark:bg-sky-950">
                <div className="font-medium text-slate-900 dark:text-slate-50">{context.next_decision.decision}</div>
                <div className="text-xs text-slate-700 dark:text-slate-200">
                  Owner: {humanize(context.next_decision.owner_role)}{context.next_decision.detail ? ` · ${context.next_decision.detail}` : ""}
                </div>
              </div>
            </>
          )}

          {status}

          <PanelHeading>Actions</PanelHeading>
          {context.actions.length ? (
            <ul className="space-y-2" data-testid="ws-actions">
              {context.actions.map((a) => <ActionItem key={a.id} action={a} onAction={onAction} busy={busy} />)}
            </ul>
          ) : <p className="text-sm text-slate-500">No actions available.</p>}

          {children}

          {context.links.some((l) => isSafeHref(l.href)) && (
            <>
              <PanelHeading>Open in the Classic Experience</PanelHeading>
              <ul className="space-y-0.5 text-sm" data-testid="ws-links">
                {context.links.filter((l) => isSafeHref(l.href)).map((l) => (
                  <li key={l.href}><Link href={l.href} className="text-sky-700 underline dark:text-sky-300">{l.label}</Link></li>
                ))}
              </ul>
            </>
          )}

          <PanelHeading>Data labels</PanelHeading>
          <ul className="space-y-1" data-testid="ws-data-labels">
            {context.data_labels.map((label) => (
              <li key={label} className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-950 dark:bg-amber-950 dark:text-amber-100">{label}</li>
            ))}
          </ul>

          <PanelHeading>Your identity</PanelHeading>
          <p className="text-sm text-slate-800 dark:text-slate-100" data-testid="ws-identity">
            {context.identity.display_name}
            <span className="ml-1 text-xs text-slate-500">({context.identity.roles.map(humanize).join(", ")})</span>
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400">Results only include scopes your roles can read.</p>
        </div>
      )}
    </Panel>
  );
}
