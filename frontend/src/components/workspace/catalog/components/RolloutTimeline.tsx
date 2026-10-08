"use client";

import { StatusBadge } from "@/components/ui";
import { humanize } from "@/lib/format";
import { SafeLink } from "../SafeLink";
import { Chip, defineDomainComponent, Facts, Muted, Panel, type DomainViewProps } from "./shared";

const STAGE_STATUS: Record<string, { icon: string; label: string; cls: string }> = {
  DONE: { icon: "✓", label: "Done", cls: "border-slate-200 dark:border-slate-700" },
  CURRENT: { icon: "●", label: "Current", cls: "border-sky-500 ring-1 ring-sky-500 dark:border-sky-400 dark:ring-sky-400" },
  PLANNED: { icon: "○", label: "Planned", cls: "border-slate-200 dark:border-slate-700" },
  NOT_PLANNED: { icon: "–", label: "Not planned", cls: "border-dashed border-slate-300 text-slate-500 dark:border-slate-700 dark:text-slate-400" },
};

function RolloutTimelineView({ data: d, panel }: DomainViewProps<"RolloutTimeline">) {
  return (
    <Panel {...panel}
      meta={d.suggested ? <Chip tone="warning" icon="!">Suggested template, not saved</Chip> : d.plan ? <StatusBadge value={d.plan.state} /> : undefined}>
      {d.plan ? (
        <Facts items={[
          ["Plan", <SafeLink key="p" href={d.plan.href}>{d.plan.title}</SafeLink>],
          ["Plan id", d.plan.id],
          ["Stage", humanize(d.plan.stage)],
          ["State", <StatusBadge key="s" value={d.plan.state} />],
          ["Target scope", d.plan.target_scope_id],
        ]} />
      ) : <Muted>No active rollout plan{d.suggested ? "; the stages below are an unsaved suggestion." : "."}</Muted>}
      <ol className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4" aria-label="Rollout stages">
        {d.stages.map((s, i) => {
          const st = STAGE_STATUS[s.status];
          return (
            <li key={`${s.stage}-${i}`} className={`rounded-md border p-3 ${st.cls}`} data-stage-status={s.status}
                aria-current={s.status === "CURRENT" ? "step" : undefined}>
              <div className="flex items-center gap-1.5 text-xs font-medium text-slate-600 dark:text-slate-300">
                <span aria-hidden="true">{st.icon}</span>{st.label}
              </div>
              <div className="mt-1 font-semibold text-slate-900 dark:text-slate-50">{humanize(s.stage)}</div>
              <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {s.scope_ids.length ? s.scope_ids.join(", ") : "No scopes assigned"}
              </div>
              {s.settings_summary && s.settings_summary !== "-" && (
                <div className="mt-1 break-words font-mono text-xs text-slate-600 dark:text-slate-300">{s.settings_summary}</div>
              )}
            </li>
          );
        })}
      </ol>
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const RolloutTimeline = defineDomainComponent("RolloutTimeline", RolloutTimelineView);
