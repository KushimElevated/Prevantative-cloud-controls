"use client";

import { DataTable, StatusBadge } from "@/components/ui";
import { fmtTime, humanize, leaf } from "@/lib/format";
import { SafeLink } from "../SafeLink";
import type { DataOf } from "../contracts";
import { Callout, Chip, defineDomainComponent, Muted, Panel, type DomainViewProps } from "./shared";

function expiry(days: number): string {
  const n = Math.trunc(days);
  if (days < 0) return `expired ${Math.abs(n)} day${Math.abs(n) === 1 ? "" : "s"} ago`;
  return `in ${n} day${n === 1 ? "" : "s"}`;
}

/** The resources an exception names; the server lists at most a few ids and reports the full count. */
function resources(e: DataOf<"ExceptionReview">["items"][number]): string {
  const shown = e.resource_ids.map(leaf).join(", ");
  const more = (e.resource_count ?? e.resource_ids.length) - e.resource_ids.length;
  if (more <= 0) return shown ? `: ${shown}` : "";
  return `: ${shown ? `${shown} and ` : ""}${more} more (${e.resource_count} resources)`;
}

function ExceptionReviewView({ data: d, panel }: DomainViewProps<"ExceptionReview">) {
  const counts = Object.entries(d.counts_by_status);
  const classic = d.links.find((l) => l.href.startsWith("/exceptions"))?.href ?? "/exceptions";
  return (
    <Panel {...panel} links={d.links}
      meta={counts.length > 0 ? counts.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1"><StatusBadge value={k} /><span className="text-xs font-semibold tabular-nums">{v}</span></span>
      )) : undefined}>
      {d.truncated && (
        <Callout tone="warning" testId="ws-exceptions-truncated"
                 title={(d.total ?? 0) > d.items.length
                   ? `Showing ${d.items.length} of ${d.total} exceptions`
                   : `Showing the first ${d.items.length} exceptions`}>
          <SafeLink href={classic}>See every exception in the Classic Experience</SafeLink>
        </Callout>
      )}
      <DataTable rows={d.items} rowKey={(e) => e.id} caption="Exceptions" empty="No exceptions for this control in the scopes you can read." columns={[
        { key: "id", header: "Exception", render: (e) => (
          <span>
            <SafeLink href={e.href}>{e.id}</SafeLink>
            <div className="text-xs text-slate-500 dark:text-slate-400">requested by {e.requester_id}</div>
          </span>
        ) },
        { key: "app", header: "Application / scope", render: (e) => (
          <span>
            {e.application}
            <div className="text-xs text-slate-500 dark:text-slate-400">{e.scope_id}</div>
            <div className="text-xs text-slate-500 dark:text-slate-400">
              {humanize(e.granularity)}{resources(e)}
            </div>
          </span>
        ) },
        { key: "gov", header: "Governance", render: (e) => <StatusBadge value={e.effective_status} /> },
        { key: "nat", header: "Native", render: (e) => <StatusBadge value={e.native_status} /> },
        { key: "disp", header: "Disposition", render: (e) => <StatusBadge value={e.disposition} /> },
        { key: "rep", header: "Representable", render: (e) => <StatusBadge value={e.representability} /> },
        { key: "exp", header: "Expires", render: (e) => (
          <span>
            {fmtTime(e.expires_at)}
            {e.expires_at && <div className="text-xs text-slate-500 dark:text-slate-400">{expiry(e.days_until_expiry)}</div>}
            {e.expiring_soon && <div className="mt-0.5"><Chip tone="warning" icon="!">Expiring soon</Chip></div>}
          </span>
        ) },
      ]} />
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const ExceptionReview = defineDomainComponent("ExceptionReview", ExceptionReviewView);
