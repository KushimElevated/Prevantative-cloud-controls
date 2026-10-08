"use client";

import Link from "next/link";
import { ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { humanize } from "@/lib/format";

/* ------------------------------------------------------------------ status labels */

type Tone = "good" | "warning" | "serious" | "critical" | "neutral" | "info";

const TONES: Record<string, Tone> = {
  COMPLIANT: "good", READY: "good", VERIFIED: "good", APPROVED: "good", PASS: "good", VALID: "good",
  MATCH: "good", EFFECTIVE: "good", REPRESENTABLE: "good", VERIFIED_PRIMARY_SOURCE: "good", ACTIVE: "good",
  VERIFIED_PROTECTED: "good", CLOSED: "neutral", SATISFIED: "good", COMPLETED: "good",
  UNKNOWN: "warning", PENDING: "warning", IN_REVIEW: "warning", APPROVED_UNAPPLIED: "warning", DRAFT: "info",
  EXPORTED: "info", ACCEPTED: "info", APPLIED: "info", STALE: "warning", REQUESTED: "warning",
  SECURITY_REVIEW: "warning", WARN: "warning", PAUSED: "warning", UNVERIFIED: "warning", OPEN: "warning",
  UNKNOWN_ENFORCEMENT: "warning", REMOVAL_PENDING: "warning", OUT_OF_ORDER: "warning", NOT_SATISFIED: "serious",
  NON_COMPLIANT: "serious", BLOCKED: "serious", EXPIRED: "serious", DRIFTED: "serious", PREDICTED_DENIED: "serious",
  MISMATCH: "serious", NOT_ENFORCED: "serious", REVOKED: "serious",
  FAILED: "critical", FAIL: "critical", REJECTED: "critical", UNSUPPORTED: "critical",
  REQUIRES_DIFFERENT_MECHANISM: "critical", MISSING: "critical", REJECTED_DIGEST_MISMATCH: "critical",
  REJECTED_SCOPE_MISMATCH: "critical", REJECTED_NOT_AUTHORIZED: "critical",
  NOT_APPLICABLE: "neutral", NONE: "neutral", SUPERSEDED: "neutral", RETIRED: "neutral", CANCELLED: "neutral",
  NOT_DENIED_BY_THIS_CONTROL: "neutral", NOT_EXPORTED: "neutral", NOT_REQUESTED: "neutral", DUPLICATE: "neutral",
};

// Icon + text: status is never conveyed by color alone.
const ICONS: Record<Tone, string> = { good: "✓", warning: "!", serious: "▲", critical: "✕", neutral: "–", info: "•" };
const ICON_COLOR: Record<Tone, string> = {
  good: "text-status-good", warning: "text-status-warning", serious: "text-status-serious",
  critical: "text-status-critical", neutral: "text-slate-400", info: "text-sky-600",
};

export function toneFor(value: string | null | undefined): Tone {
  return (value && TONES[value]) || "neutral";
}

export function StatusBadge({ value, label }: { value: string | null | undefined; label?: string }) {
  if (!value) return <span className="text-slate-400">-</span>;
  const tone = toneFor(value);
  return (
    <span
      className="inline-flex items-center gap-1 whitespace-nowrap rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs font-medium text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
      data-status={value}
    >
      <span aria-hidden="true" className={ICON_COLOR[tone]}>
        {ICONS[tone]}
      </span>
      {label ?? humanize(value)}
    </span>
  );
}

/* ------------------------------------------------------------------ layout */

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-50">{title}</h1>
        {subtitle && <div className="mt-1 text-sm text-slate-600 dark:text-slate-300">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Section({ title, id, children, actions, description }: {
  title: string; id?: string; children: ReactNode; actions?: ReactNode; description?: ReactNode;
}) {
  return (
    <section id={id} className="mb-8 rounded-lg border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900 dark:text-slate-50">{title}</h2>
          {description && <p className="mt-0.5 text-sm text-slate-600 dark:text-slate-300">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

export function KeyValue({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm sm:grid-cols-[200px_1fr]">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="font-medium text-slate-500 dark:text-slate-400">{k}</dt>
          <dd className="text-slate-900 dark:text-slate-100">{v ?? "-"}</dd>
        </div>
      ))}
    </dl>
  );
}

/* ------------------------------------------------------------------ figures */

export function StatTile({ label, value, sub, href, testId }: {
  label: string; value: ReactNode; sub?: ReactNode; href?: string; testId?: string;
}) {
  const body = (
    <div className="h-full rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-700 dark:bg-slate-900" data-testid={testId}>
      <div className="text-sm text-slate-600 dark:text-slate-300">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-slate-900 dark:text-slate-50">{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</div>}
    </div>
  );
  return href ? (
    <Link href={href} className="block hover:ring-2 hover:ring-sky-300 focus:outline-none focus:ring-2 focus:ring-sky-500 rounded-lg">
      {body}
    </Link>
  ) : body;
}

/** Ratio meter. Empty denominators render N/A, never 0%. Track is a lighter step of the fill hue. */
export function Meter({ numerator, denominator, label }: { numerator: number; denominator: number; label: string }) {
  if (denominator === 0) {
    return <div className="text-sm text-slate-500" aria-label={`${label}: not applicable`}>N/A (no denominator)</div>;
  }
  const pct = Math.round((100 * numerator) / denominator);
  return (
    <div>
      <div className="h-2 w-full rounded bg-sky-100 dark:bg-sky-950" role="meter" aria-valuemin={0}
           aria-valuemax={denominator} aria-valuenow={numerator} aria-label={label}>
        <div className="h-2 rounded bg-sky-600" style={{ width: `${pct}%` }} />
      </div>
      <div className="mt-1 text-xs text-slate-600 dark:text-slate-300">
        {numerator} of {denominator} ({pct}%)
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ tables */

export type Column<T> = { key: string; header: string; render: (row: T) => ReactNode; className?: string };

export function DataTable<T>({ rows, columns, empty, rowKey, caption }: {
  rows: T[]; columns: Column<T>[]; empty?: string; rowKey: (row: T) => string; caption?: string;
}) {
  if (!rows.length) return <Empty message={empty ?? "Nothing to show."} />;
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead>
          <tr className="border-b border-slate-200 text-left text-xs uppercase tracking-wide text-slate-500 dark:border-slate-700 dark:text-slate-400">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={`px-2 py-2 font-medium ${c.className ?? ""}`}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} className="border-b border-slate-100 align-top hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
              {columns.map((c) => (
                <td key={c.key} className={`px-2 py-2 text-slate-800 dark:text-slate-100 ${c.className ?? ""}`}>{c.render(r)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ total, limit, offset, onChange }: {
  total: number; limit: number; offset: number; onChange: (offset: number) => void;
}) {
  if (total <= limit) return null;
  return (
    <div className="mt-3 flex items-center gap-3 text-sm text-slate-600">
      <Button variant="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Previous</Button>
      <span>{offset + 1}-{Math.min(offset + limit, total)} of {total}</span>
      <Button variant="secondary" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next</Button>
    </div>
  );
}

/* ------------------------------------------------------------------ states */

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div role="status" className="py-6 text-sm text-slate-500">{what}…</div>;
}

export function Empty({ message }: { message: string }) {
  return <div className="rounded border border-dashed border-slate-300 p-4 text-sm text-slate-500 dark:border-slate-700">{message}</div>;
}

export function ErrorBox({ error }: { error: ApiError | Error | null }) {
  if (!error) return null;
  const e = error as ApiError;
  const details = e.details;
  return (
    <div role="alert" className="my-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950 dark:text-red-100">
      <div className="font-medium">
        <span aria-hidden="true">✕ </span>
        {e.code ? `${e.code}: ` : ""}{e.message}
      </div>
      {Array.isArray(details) && details.length > 0 && (
        <ul className="mt-1 list-disc pl-5">
          {details.slice(0, 12).map((d: any, i: number) => (
            <li key={i}>{typeof d === "string" ? d : d.label ? `${d.label}: ${JSON.stringify(d.detail)}` : d.id ? `${d.id}: ${typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail ?? d.message ?? "")}` : JSON.stringify(d)}</li>
          ))}
        </ul>
      )}
      {e.correlationId && <div className="mt-1 text-xs opacity-75">Correlation id: {e.correlationId}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ controls */

export function Button({ children, onClick, disabled, variant = "primary", type = "button", testId }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; variant?: "primary" | "secondary" | "danger";
  type?: "button" | "submit"; testId?: string;
}) {
  const styles = {
    primary: "bg-sky-700 text-white hover:bg-sky-800 disabled:bg-slate-300",
    secondary: "border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:text-slate-400 dark:bg-slate-800 dark:text-slate-100 dark:border-slate-600",
    danger: "bg-red-700 text-white hover:bg-red-800 disabled:bg-slate-300",
  }[variant];
  return (
    <button type={type} onClick={onClick} disabled={disabled} data-testid={testId}
            className={`rounded px-3 py-1.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed ${styles}`}>
      {children}
    </button>
  );
}

export function Select({ label, value, onChange, options, testId }: {
  label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[]; testId?: string;
}) {
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId}
              className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

export function TextInput({ label, value, onChange, testId, multiline, placeholder }: {
  label: string; value: string; onChange: (v: string) => void; testId?: string; multiline?: boolean; placeholder?: string;
}) {
  const cls = "rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100";
  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300">
      {label}
      {multiline ? (
        <textarea value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId} rows={3} className={cls} placeholder={placeholder} />
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} data-testid={testId} className={cls} placeholder={placeholder} />
      )}
    </label>
  );
}

export function CodeBlock({ value, maxHeight = "24rem" }: { value: unknown; maxHeight?: string }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="overflow-auto rounded bg-slate-50 p-3 text-xs text-slate-800 dark:bg-slate-950 dark:text-slate-200" style={{ maxHeight }}>
      {text}
    </pre>
  );
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "warning" }) {
  const cls = tone === "warning"
    ? "border-amber-300 bg-amber-50 text-amber-950 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100"
    : "border-sky-200 bg-sky-50 text-sky-950 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-100";
  return <div className={`rounded border p-3 text-sm ${cls}`}>{children}</div>;
}
