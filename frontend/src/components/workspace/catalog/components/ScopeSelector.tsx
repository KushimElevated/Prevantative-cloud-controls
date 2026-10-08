"use client";

import { useEffect, useState } from "react";
import { humanize } from "@/lib/format";
import { Chip, defineDomainComponent, Muted, Panel, type DomainViewProps } from "./shared";

function ScopeSelectorView({ data: d, panel, emit }: DomainViewProps<"ScopeSelector">) {
  const selected = d.selected_scope_id ?? "";
  const [value, setValue] = useState(selected);
  useEffect(() => setValue(selected), [selected]);
  const selectId = `ws-scope-${panel.componentId}`;
  const current = d.options.find((o) => o.scope_id === selected);
  const onChange = (next: string) => {
    setValue(next);
    if (next && next !== selected) emit("ws.select_scope", { scope_id: next });
  };
  return (
    <Panel {...panel} meta={d.provider ? <Chip>{humanize(d.provider)}</Chip> : undefined}>
      {d.options.length === 0 ? <Muted>No scopes are readable with your identity.</Muted> : (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex min-w-[16rem] flex-col gap-1">
            <label htmlFor={selectId} className="text-xs font-medium text-slate-600 dark:text-slate-300">Scope</label>
            <select id={selectId} value={value} onChange={(e) => onChange(e.target.value)} data-testid="ws-scope-select"
                    className="rounded border border-slate-300 bg-white px-2 py-1.5 font-mono text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100">
              {!selected && <option value="" disabled>Select a scope…</option>}
              {d.options.map((o) => (
                <option key={o.scope_id} value={o.scope_id}>
                  {"  ".repeat(Math.max(0, Math.min(o.depth, 8)))}{o.name} ({o.scope_id}){o.environment ? ` · ${o.environment}` : ""}
                </option>
              ))}
            </select>
          </div>
          {current && (
            <span className="pb-1.5 text-xs text-slate-500 dark:text-slate-400">
              {humanize(current.scope_type)}{current.environment ? ` · ${current.environment}` : ""}
            </span>
          )}
        </div>
      )}
      <Muted>{d.note}</Muted>
    </Panel>
  );
}

export const ScopeSelector = defineDomainComponent("ScopeSelector", ScopeSelectorView);
