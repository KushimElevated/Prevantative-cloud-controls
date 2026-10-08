"use client";

import Link from "next/link";
import { FormEvent, KeyboardEvent, useId } from "react";
import { ApiError } from "@/lib/api";
import { humanize } from "@/lib/format";
import { ErrorBox, Notice, StatusBadge } from "@/components/ui";
import {
  Finding, FindingKind, Investigation, isSafeHref, Origin, Summary, WorkspaceMode,
} from "@/lib/workspace";
import { Panel, PanelHeading } from "./Panel";

export const FINDING_KINDS: { kind: FindingKind; label: string; icon: string; hint: string; tone: string }[] = [
  { kind: "EVIDENCE", label: "Verified evidence", icon: "✓", hint: "Established from persisted platform records.",
    tone: "text-status-good" },
  { kind: "ESTIMATE", label: "Estimated impact", icon: "≈", hint: "Predicted from supplied fixtures only.",
    tone: "text-sky-600" },
  { kind: "UNKNOWN", label: "Unknown", icon: "?", hint: "Cannot be established from the available evidence.",
    tone: "text-status-warning" },
  { kind: "RECOMMENDATION", label: "Recommendation", icon: "→", hint: "Next steps. Nothing happens without your confirmation.",
    tone: "text-violet-600 dark:text-violet-300" },
];

const STEP_STATUS: Record<string, string> = { ok: "COMPLETED", error: "FAILED", rejected: "REJECTED" };

export type PinnedContext = { control_id?: string; scope_id?: string };

export function OriginBadge({ origin, model }: { origin: Origin; model?: string | null }) {
  if (origin === "ai") {
    return (
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="inline-flex items-center gap-1 rounded border border-violet-300 bg-violet-50 px-1.5 py-0.5 text-xs font-medium text-violet-900 dark:border-violet-800 dark:bg-violet-950 dark:text-violet-100">
          <span aria-hidden="true">✦</span>AI-generated — verify against the canvas
        </span>
        {model && <span className="text-xs text-slate-500 dark:text-slate-400">Model: <code>{model}</code></span>}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-xs font-medium text-slate-800 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100">
      <span aria-hidden="true">▣</span>Platform (rule-based)
    </span>
  );
}

function SummaryBlock({ summary }: { summary: Summary }) {
  return (
    <div data-testid="ws-summary" data-origin={summary.origin}
         className={`rounded border p-3 ${summary.origin === "ai" ? "border-violet-200 dark:border-violet-900" : "border-slate-200 dark:border-slate-700"}`}>
      <OriginBadge origin={summary.origin} model={summary.model} />
      <p className="mt-2 text-sm text-slate-900 dark:text-slate-100">{summary.text}</p>
      {summary.origin === "ai" && summary.label && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{summary.label}</p>}
    </div>
  );
}

function FindingItem({ finding }: { finding: Finding }) {
  const href = finding.source?.href;
  return (
    <li className="text-sm text-slate-800 dark:text-slate-100" data-kind={finding.kind} data-origin={finding.origin}>
      {(finding.kind === "RECOMMENDATION" || finding.origin === "ai") && (
        <span className={`mr-1.5 inline-block rounded px-1 text-[10px] font-semibold uppercase tracking-wide ${finding.origin === "ai"
          ? "bg-violet-100 text-violet-900 dark:bg-violet-900 dark:text-violet-100"
          : "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-200"}`}>
          {finding.origin === "ai" ? "AI" : "Platform"}
        </span>
      )}
      {finding.text}
      {isSafeHref(href) && (
        <>
          {" "}
          <Link href={href} className="whitespace-nowrap text-xs text-sky-700 underline dark:text-sky-300">
            Open {humanize(finding.source?.type ?? "source").toLowerCase()}
          </Link>
        </>
      )}
    </li>
  );
}

export function Findings({ findings }: { findings: Finding[] }) {
  return (
    <div data-testid="ws-findings" className="space-y-3">
      {FINDING_KINDS.map(({ kind, label, icon, hint, tone }) => {
        const items = findings.filter((f) => f.kind === kind);
        if (!items.length) return null;
        return (
          <section key={kind} data-testid={`ws-findings-${kind}`} aria-label={label}>
            <div className="flex items-baseline gap-1.5">
              <span aria-hidden="true" className={`font-semibold ${tone}`}>{icon}</span>
              <h4 className="text-sm font-semibold text-slate-900 dark:text-slate-50">{label}</h4>
              <span className="text-xs text-slate-500">({items.length})</span>
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400">{hint}</p>
            <ul className="mt-1 list-disc space-y-1.5 pl-5 marker:text-slate-400">
              {items.map((f, i) => <FindingItem key={i} finding={f} />)}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function Interpretation({ investigation }: { investigation: Investigation }) {
  const { intent } = investigation;
  const e = intent.entities ?? {};
  return (
    <div className="text-xs text-slate-600 dark:text-slate-300" data-testid="ws-interpretation">
      <div>Interpreted as <span className="font-medium text-slate-800 dark:text-slate-100">{intent.label}</span></div>
      {e.control_id && <div>Control <code>{e.control_id}</code>{e.control_reason ? ` (${e.control_reason})` : ""}</div>}
      {e.scope_id && <div>Scope <code>{e.scope_id}</code>{e.scope_reason ? ` (${e.scope_reason})` : ""}</div>}
      {intent.notes?.map((n, i) => <div key={i}>{n}</div>)}
    </div>
  );
}

function Steps({ steps }: { steps: Investigation["steps"] }) {
  return (
    <details data-testid="ws-steps" className="rounded border border-slate-200 p-2 text-sm dark:border-slate-700">
      <summary className="cursor-pointer text-sky-700 dark:text-sky-300">How this was investigated ({steps.length} steps)</summary>
      <ol className="mt-2 list-decimal space-y-1 pl-5">
        {steps.map((s, i) => (
          <li key={i} className="text-xs text-slate-700 dark:text-slate-200">
            <span className="flex flex-wrap items-center gap-1.5">
              <code className="text-slate-900 dark:text-slate-50">{s.tool}</code>
              <StatusBadge value={STEP_STATUS[s.status] ?? "UNKNOWN"} label={s.status} />
            </span>
            <span>{s.label}</span>
            {s.detail && <span className="block text-slate-500 dark:text-slate-400">{s.detail}</span>}
          </li>
        ))}
      </ol>
    </details>
  );
}

function ModeSwitch({ mode, onChange, ai, disabled }: {
  mode: WorkspaceMode; onChange: (m: WorkspaceMode) => void;
  ai: { available: boolean; reason: string | null; model: string | null }; disabled?: boolean;
}) {
  const reasonId = useId();
  const btn = (active: boolean) => `rounded px-2.5 py-1 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-50 ${active
    ? "bg-white text-slate-900 shadow-sm dark:bg-slate-950 dark:text-slate-50"
    : "text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-slate-50"}`;
  return (
    <div>
      <div role="group" aria-label="Investigation mode" className="inline-flex rounded border border-slate-300 bg-slate-50 p-0.5 dark:border-slate-600 dark:bg-slate-800">
        <button type="button" data-testid="ws-mode-deterministic" aria-pressed={mode === "deterministic"}
                disabled={disabled} onClick={() => onChange("deterministic")} className={btn(mode === "deterministic")}>
          Deterministic
        </button>
        <button type="button" data-testid="ws-mode-ai" aria-pressed={mode === "ai"}
                disabled={disabled || !ai.available} aria-describedby={reasonId}
                onClick={() => onChange("ai")} className={btn(mode === "ai")}>
          AI-assisted
        </button>
      </div>
      <p id={reasonId} className="mt-1 text-xs text-slate-500 dark:text-slate-400" data-testid="ws-mode-help">
        {!ai.available
          ? (ai.reason ?? "AI-assisted mode is unavailable.")
          : mode === "ai"
            ? `A model${ai.model ? ` (${ai.model})` : ""} picks read-only tools and writes a summary; the canvas stays authoritative.`
            : "Rule-based interpretation. No model is called."}
      </p>
    </div>
  );
}

export function InvestigationPanel({ question, onQuestionChange, onSubmit, examples, onExample, mode, onModeChange, ai,
  pinned, onClearPin, running, error, investigation }: {
  question: string; onQuestionChange: (q: string) => void; onSubmit: () => void;
  examples: string[]; onExample: (q: string) => void;
  mode: WorkspaceMode; onModeChange: (m: WorkspaceMode) => void;
  ai: { available: boolean; reason: string | null; model: string | null };
  pinned: PinnedContext; onClearPin: (key: keyof PinnedContext) => void;
  running: boolean; error: ApiError | null; investigation: Investigation | null;
}) {
  const questionId = useId();
  const canSubmit = !running && question.trim().length >= 3;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (canSubmit) onSubmit();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canSubmit) {
      e.preventDefault();
      onSubmit();
    }
  };
  const pins = (Object.entries(pinned) as [keyof PinnedContext, string | undefined][]).filter(([, v]) => v);

  return (
    <Panel title="Investigation" testId="ws-investigation" busy={running}
           description="Ask a question. Answers are built from read-only platform tools within your scopes.">
      <form onSubmit={submit} className="space-y-2">
        <label htmlFor={questionId} className="block text-xs font-medium text-slate-600 dark:text-slate-300">
          Ask about a control, scope or rollout
        </label>
        <textarea id={questionId} data-testid="ws-question" value={question} rows={3} maxLength={500}
                  onChange={(e) => onQuestionChange(e.target.value)} onKeyDown={onKeyDown}
                  placeholder="e.g. What would happen if we prevented public network access in production?"
                  className="w-full rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100" />
        {pins.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 text-xs text-slate-600 dark:text-slate-300">
            <span>Within:</span>
            {pins.map(([key, value]) => (
              <span key={key} className="inline-flex items-center gap-1 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 dark:border-slate-700 dark:bg-slate-800">
                {key === "control_id" ? "control" : "scope"} <code>{value}</code>
                <button type="button" onClick={() => onClearPin(key)} data-testid={`ws-pin-clear-${key}`}
                        aria-label={`Remove ${key === "control_id" ? "control" : "scope"} ${value} from the question context`}
                        className="rounded px-0.5 text-slate-500 hover:text-slate-900 focus:outline-none focus:ring-2 focus:ring-sky-500 dark:hover:text-slate-50">
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <ModeSwitch mode={mode} onChange={onModeChange} ai={ai} disabled={running} />
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" data-testid="ws-submit" disabled={!canSubmit}
                  className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-800 focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed disabled:bg-slate-300">
            {running ? "Investigating…" : "Investigate"}
          </button>
          <span role="status" aria-live="polite" className="text-xs text-slate-500 dark:text-slate-400" data-testid="ws-status">
            {running ? "Investigating…" : investigation ? `${investigation.findings.length} findings, ${investigation.steps.length} steps.` : ""}
          </span>
        </div>
      </form>

      {examples.length > 0 && (
        <details className="mt-3" open={!investigation}>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Example questions</summary>
          <ul className="mt-1.5 space-y-1">
            {examples.map((ex, i) => (
              <li key={i}>
                <button type="button" data-testid={`ws-example-${i}`} disabled={running} onClick={() => onExample(ex)}
                        className="w-full rounded border border-slate-200 px-2 py-1 text-left text-xs text-slate-700 hover:border-sky-400 hover:bg-sky-50 focus:outline-none focus:ring-2 focus:ring-sky-500 disabled:cursor-not-allowed disabled:opacity-60 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800">
                  {ex}
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}

      <ErrorBox error={error} />

      {investigation && (
        <div className="mt-4 space-y-3 border-t border-slate-200 pt-3 dark:border-slate-700">
          {investigation.mode_note && <Notice tone="warning"><span data-testid="ws-mode-note">{investigation.mode_note}</span></Notice>}
          <PanelHeading>Summary</PanelHeading>
          <SummaryBlock summary={investigation.summary} />
          <Interpretation investigation={investigation} />
          <PanelHeading>Findings</PanelHeading>
          <Findings findings={investigation.findings} />
          <Steps steps={investigation.steps} />
        </div>
      )}
    </Panel>
  );
}
