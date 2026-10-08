"use client";

import { useCallback, type ComponentType, type ReactNode } from "react";
import { createComponentImplementation, type ReactComponentImplementation } from "@a2ui/react/v0_9";
import type { z } from "zod";
import { StatusBadge } from "@/components/ui";
import { humanize } from "@/lib/format";
import type { CanvasActionName } from "../../canvas-types";
import {
  DATA_CONTRACTS,
  DomainProps,
  type ClientActionContext,
  type DataOf,
  type DomainComponentName,
  type LinkData,
} from "../contracts";
import { SafeLink } from "../SafeLink";

/* ------------------------------------------------------------------ domain component factory */

export type Emit = <N extends CanvasActionName>(name: N, context: ClientActionContext<N>) => void;

export type PanelIdentity = { name: string; title: string; componentId: string };

export type DomainViewProps<N extends DomainComponentName> = { data: DataOf<N>; panel: PanelIdentity; emit: Emit };

export const INVALID_DATA_MESSAGE = "This panel's data failed validation and was not displayed.";

/**
 * Wraps a view in the A2UI binder. The bound view is parsed with the component's data contract on every
 * render; when it does not conform, only the alert card renders (unvalidated data is never displayed).
 */
export function defineDomainComponent<N extends DomainComponentName>(
  name: N,
  View: ComponentType<DomainViewProps<N>>,
): ReactComponentImplementation {
  return createComponentImplementation({ name, schema: DomainProps }, ({ props, context }) => {
    const componentId = context.componentModel.id;
    const emit = useCallback<Emit>((action, ctx) => {
      void context.dispatchAction({ event: { name: action, context: ctx } }).catch(() => undefined);
    }, [context]);
    const panel = { name, title: props.title, componentId };
    const parsed = DATA_CONTRACTS[name].safeParse(props.data as unknown) as z.SafeParseReturnType<unknown, DataOf<N>>;
    if (!parsed.success) {
      console.warn(`A2UI ${name} (${componentId}): data contract failed`, parsed.error.issues.slice(0, 10));
      return (
        <Panel {...panel}>
          <InvalidData fields={parsed.error.issues.map((i) => i.path.join(".") || "(root)")} />
        </Panel>
      );
    }
    return <View data={parsed.data} panel={panel} emit={emit} />;
  });
}

function InvalidData({ fields }: { fields: string[] }) {
  const unique = Array.from(new Set(fields)).slice(0, 5);
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
      <div className="font-medium"><span aria-hidden="true">✕ </span>{INVALID_DATA_MESSAGE}</div>
      {unique.length > 0 && <div className="mt-1 text-xs opacity-80">Fields: {unique.join(", ")}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ panel */

export function Panel({ name, title, componentId, subtitle, meta, links, children }: PanelIdentity & {
  subtitle?: ReactNode; meta?: ReactNode; links?: LinkData[]; children: ReactNode;
}) {
  const headingId = `ws-h-${componentId}`;
  return (
    <section data-testid={`ws-component-${name}`} data-component-id={componentId} aria-labelledby={headingId}
             className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-slate-100 bg-slate-50/70 px-4 py-3 dark:border-slate-800 dark:bg-slate-900/60">
        <div className="min-w-0">
          <h3 id={headingId} className="text-sm font-semibold tracking-tight text-slate-900 dark:text-slate-50">{title}</h3>
          {subtitle && <div className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{subtitle}</div>}
        </div>
        {meta && <div className="flex flex-wrap items-center gap-1.5">{meta}</div>}
      </header>
      <div className="space-y-4 px-4 py-4 text-sm text-slate-800 dark:text-slate-100">{children}</div>
      {links && links.length > 0 && (
        <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-slate-100 px-4 py-2 text-xs dark:border-slate-800">
          {links.map((l, i) => (
            <SafeLink key={`${l.href}-${i}`} href={l.href}>{l.label}<span aria-hidden="true"> →</span></SafeLink>
          ))}
        </footer>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ building blocks */

export function SubHeading({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
      {hint && <span className="ml-1 font-normal normal-case tracking-normal text-slate-500 dark:text-slate-400">{hint}</span>}
    </h4>
  );
}

export function Facts({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[minmax(8rem,11rem)_1fr]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="min-w-0 break-words text-slate-900 dark:text-slate-100">{v ?? "-"}</dd>
        </div>
      ))}
    </dl>
  );
}

type ChipTone = "neutral" | "emphasis" | "warning";
const CHIP: Record<ChipTone, string> = {
  neutral: "border-slate-200 bg-white text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  emphasis: "border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100",
  warning: "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100",
};

export function Chip({ children, tone = "neutral", icon, testId }: { children: ReactNode; tone?: ChipTone; icon?: string; testId?: string }) {
  return (
    <span data-testid={testId} className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium ${CHIP[tone]}`}>
      {icon && <span aria-hidden="true">{icon}</span>}
      {children}
    </span>
  );
}

const PROVENANCE: Record<string, string> = {
  FIXTURE: "Demo fixture data",
  MOCK_PIPELINE: "Mock pipeline data",
  MANUAL: "Manually supplied",
  LIVE: "Live observation",
  PERSISTED: "Persisted platform record",
};

export function provenanceLabel(value: string | null | undefined): string {
  if (!value) return "Provenance not recorded";
  return PROVENANCE[value] ?? humanize(value);
}

export function ProvenanceChip({ value }: { value: string | null | undefined }) {
  const demo = value === "FIXTURE" || value === "MOCK_PIPELINE";
  return <Chip tone={demo ? "warning" : "neutral"} icon={demo ? "◆" : undefined}>{provenanceLabel(value)}</Chip>;
}

type CalloutTone = "info" | "warning" | "critical";
const CALLOUT: Record<CalloutTone, { cls: string; icon: string; label: string }> = {
  info: { cls: "border-sky-200 bg-sky-50 text-sky-950 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100", icon: "•", label: "Note" },
  warning: { cls: "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100", icon: "!", label: "Warning" },
  critical: { cls: "border-red-300 bg-red-50 text-red-950 dark:border-red-900 dark:bg-red-950 dark:text-red-100", icon: "✕", label: "Critical" },
};

export function Callout({ tone = "info", title, children, testId }: { tone?: CalloutTone; title?: ReactNode; children?: ReactNode; testId?: string }) {
  const t = CALLOUT[tone];
  return (
    <div role="note" data-tone={tone} data-testid={testId} className={`flex gap-2 rounded-md border px-3 py-2 text-sm ${t.cls}`}>
      <span aria-hidden="true" className="mt-px font-bold">{t.icon}</span>
      <div className="min-w-0">
        <span className="sr-only">{t.label}: </span>
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={title ? "mt-0.5" : undefined}>{children}</div>}
      </div>
    </div>
  );
}

/** Backend-provided counts keyed by status value, shown as icon + label tiles. */
export function CountTiles({ counts, testId, caption }: { counts: Record<string, number>; testId?: string; caption?: string }) {
  const entries = Object.entries(counts);
  if (!entries.length) return <p className="text-sm text-slate-500 dark:text-slate-400">No counts reported.</p>;
  return (
    <dl className="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid={testId} aria-label={caption}>
      {entries.map(([k, v]) => (
        <div key={k} className="rounded-md border border-slate-200 px-3 py-2 dark:border-slate-700" data-count={k}>
          <dt><StatusBadge value={k} /></dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums text-slate-900 dark:text-slate-50">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ConfidenceList({ confidence }: { confidence: Record<string, { category: string; reasons: string[] }> | null | undefined }) {
  const entries = Object.entries(confidence ?? {});
  if (!entries.length) return <p className="text-sm text-slate-500 dark:text-slate-400">Confidence was not reported for the scopes you can read.</p>;
  return (
    <dl className="grid gap-2 md:grid-cols-3">
      {entries.map(([dim, c]) => (
        <div key={dim} className="rounded-md border border-slate-200 px-3 py-2 dark:border-slate-700">
          <dt className="flex flex-wrap items-center justify-between gap-1 text-xs font-medium text-slate-600 dark:text-slate-300">
            {humanize(dim)}
            <Chip>{humanize(c.category)} confidence</Chip>
          </dt>
          <dd className="mt-1 text-xs text-slate-600 dark:text-slate-300">
            {c.reasons.length ? c.reasons.join(" ") : "No reasons recorded."}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function BulletList({ items, empty }: { items: string[]; empty?: string }) {
  if (!items.length) return <p className="text-sm text-slate-500 dark:text-slate-400">{empty ?? "None recorded."}</p>;
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-sm text-slate-700 dark:text-slate-200">
      {items.map((s, i) => <li key={i}>{s}</li>)}
    </ul>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return <code title={title} className="break-all rounded bg-slate-100 px-1 py-0.5 font-mono text-xs text-slate-800 dark:bg-slate-800 dark:text-slate-200">{children}</code>;
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-xs text-slate-500 dark:text-slate-400">{children}</p>;
}

export function yesNo(value: boolean | null | undefined, unknown = "Unknown"): string {
  return value === true ? "Yes" : value === false ? "No" : unknown;
}

/** Pairs rows with their position so tables get a stable key without inventing identifiers. */
export function indexed<T>(rows: T[]): { i: number; r: T }[] {
  return rows.map((r, i) => ({ i, r }));
}
