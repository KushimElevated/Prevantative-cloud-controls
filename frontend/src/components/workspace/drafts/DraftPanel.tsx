"use client";

import Link from "next/link";
import { ReactNode, useState } from "react";
import { ApiError } from "@/lib/api";
import { humanize } from "@/lib/format";
import { Button, CodeBlock, ErrorBox, Notice, Select, StatusBadge, TextInput } from "@/components/ui";
import { Draft, DraftSubmitResult, isSafeHref, submitDraft } from "@/lib/workspace";
import { ConfirmDialog } from "../ConfirmDialog";

type Field = {
  key: string;
  label: string;
  multiline?: boolean;
  minLength?: number;
  hint?: string;
  options?: { value: string; label: string }[];
  type?: "date";
  /** The payload key this field fills when it differs from `key` (a single value sent as a one-item list). */
  fills?: string;
  /** Shown only when the prepared draft asks for it. */
  onlyWhen?: (draft: Draft) => boolean;
};

const SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((v) => ({ value: v, label: humanize(v) }));

const FIELDS: Record<Draft["kind"], Field[]> = {
  exception: [
    { key: "application", label: "Application", hint: "The resource has no application tag; name the application this exception is for.",
      onlyWhen: (d) => d.required_fields.includes("application") },
    { key: "business_justification", label: "Business justification", multiline: true, minLength: 20 },
    { key: "technical_justification", label: "Technical justification", multiline: true, minLength: 20 },
    { key: "risk_owner", label: "Risk owner" },
    { key: "compensating_controls", label: "Compensating controls (one per line)", multiline: true },
    { key: "expires_at", label: "Expires on (UTC)", type: "date" },
  ],
  control: [
    { key: "id", label: "Control id", hint: "Upper case, e.g. CTL-AZ-KV-PURGE-PROTECTION" },
    { key: "name", label: "Name" },
    { key: "security_objective", label: "Security objective", multiline: true },
    { key: "rationale", label: "Rationale", multiline: true },
    { key: "severity", label: "Severity", options: SEVERITIES },
    { key: "applicability_criteria", label: "Applicability criteria", multiline: true },
    { key: "security_owner", label: "Security owner" },
    { key: "engineering_owner", label: "Engineering owner" },
    { key: "prevention_boundary", label: "Prevention boundary", multiline: true },
    { key: "provider", label: "Provider", fills: "providers",
      options: [{ value: "", label: "Select provider" }, { value: "azure", label: "Azure" }, { value: "aws", label: "AWS" }] },
    { key: "resource_type", label: "Native resource type", hint: "e.g. Microsoft.KeyVault/vaults", fills: "resource_types" },
  ],
  rollout_plan: [],
};

const KIND_LABEL: Record<Draft["kind"], string> = {
  exception: "Exception request", rollout_plan: "Rollout plan", control: "Control proposal",
};

/** The form fields this draft shows. */
const fieldsFor = (draft: Draft) => FIELDS[draft.kind].filter((f) => !f.onlyWhen || f.onlyWhen(draft));

function preparedValues(draft: Draft): Record<string, string> {
  const p = draft.payload;
  if (draft.kind === "exception") {
    return {
      application: typeof p.application === "string" ? p.application : "",
      business_justification: p.business_justification ?? "",
      technical_justification: p.technical_justification ?? "",
      risk_owner: p.risk_owner ?? "",
      compensating_controls: (p.compensating_controls ?? []).join("\n"),
      expires_at: typeof p.expires_at === "string" ? p.expires_at.slice(0, 10) : "",
    };
  }
  if (draft.kind === "control") {
    const v: Record<string, string> = {};
    for (const f of FIELDS.control) v[f.key] = typeof p[f.key] === "string" ? p[f.key] : "";
    v.provider = p.providers?.[0] ?? "";
    v.resource_type = p.resource_types?.[0] ?? "";
    return v;
  }
  return {};
}

/**
 * Values for a freshly prepared draft. What the person already wrote into an earlier version of the same draft
 * (for example before it was refused as stale) wins over the server's suggestion; nothing else is carried over.
 */
function initialValues(draft: Draft, previous?: Record<string, string> | null): Record<string, string> {
  const v = preparedValues(draft);
  for (const f of FIELDS[draft.kind]) {
    const typed = previous?.[f.key];
    if (typeof typed === "string" && typed.trim()) v[f.key] = typed;
  }
  return v;
}

const lines = (s: string) => s.split("\n").map((l) => l.trim()).filter(Boolean);

function missingFields(draft: Draft, values: Record<string, string>, payload: Record<string, unknown>): string[] {
  const fields = fieldsFor(draft);
  const editable = fields.filter((f) => {
    const v = (values[f.key] ?? "").trim();
    if (f.key === "compensating_controls") return lines(v).length === 0;
    return v.length < (f.minLength ?? 1);
  }).map((f) => f.label);
  // Server-declared required fields this form does not edit must already be filled in the payload it submits.
  const fixed = draft.required_fields.filter((k) => !fields.some((f) => f.key === k || f.fills === k)).filter((k) => {
    const v = payload[k];
    return v === null || v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
  }).map(humanize);
  return [...editable, ...fixed];
}

/** Merges the person's edits into the server-prepared payload. Only the edited fields change. */
function buildPayload(draft: Draft, values: Record<string, string>): Record<string, unknown> {
  const p = draft.payload;
  if (draft.kind === "exception") {
    const original: string | null = typeof p.expires_at === "string" ? p.expires_at : null;
    const expires = original && original.slice(0, 10) === values.expires_at
      ? original
      : `${values.expires_at}${original ? original.slice(10) : "T00:00:00Z"}`;
    const application = fieldsFor(draft).some((f) => f.key === "application") ? { application: values.application.trim() } : {};
    return {
      ...p,
      ...application,
      business_justification: values.business_justification.trim(),
      technical_justification: values.technical_justification.trim(),
      risk_owner: values.risk_owner.trim(),
      compensating_controls: lines(values.compensating_controls),
      expires_at: expires,
    };
  }
  if (draft.kind === "control") {
    const { provider, resource_type, ...rest } = values;
    const trimmed = Object.fromEntries(Object.entries(rest).map(([k, v]) => [k, v.trim()]));
    return { ...p, ...trimmed, providers: provider ? [provider] : [], resource_types: resource_type.trim() ? [resource_type.trim()] : [] };
  }
  return p;
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-3">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</h4>
      <div className="mt-1 text-sm text-slate-800 dark:text-slate-100">{children}</div>
    </div>
  );
}

function RolloutReview({ payload }: { payload: Record<string, any> }) {
  const rb = payload.rollback_plan ?? {};
  return (
    <div data-testid="ws-draft-rollout">
      <Block title="Plan">
        <div>{payload.title}</div>
        <div className="text-xs text-slate-500">Target scope <code>{payload.target_scope_id}</code> · implementation revision <code>{payload.implementation_revision_id}</code></div>
      </Block>
      <Block title="Rings">
        <ol className="list-decimal space-y-1 pl-5">
          {(payload.rings ?? []).map((r: any, i: number) => (
            <li key={i}>
              <span className="font-medium">{humanize(r.stage)}</span> at {(r.scope_ids ?? []).map((s: string) => <code key={s} className="mr-1">{s}</code>)}
              <span className="block text-xs text-slate-500">
                {Object.entries(r.settings ?? {}).map(([k, v]) => `${k}: ${String(v)}`).join(" · ") || "No settings"}
              </span>
            </li>
          ))}
        </ol>
      </Block>
      <Block title="Pause criteria">
        <ul className="list-disc pl-5">{(payload.pause_criteria ?? []).map((c: string, i: number) => <li key={i}>{c}</li>)}</ul>
      </Block>
      <Block title="Rollback">
        <p>{rb.prior_known_good}</p>
        <ol className="mt-1 list-decimal pl-5">{(rb.steps ?? []).map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
        {(rb.limitations ?? []).length > 0 && (
          <ul className="mt-1 list-disc pl-5 text-xs text-slate-600 dark:text-slate-300">
            {rb.limitations.map((l: string, i: number) => <li key={i}>{l}</li>)}
          </ul>
        )}
        {rb.emergency_path && <p className="mt-1 text-xs">Emergency path: {rb.emergency_path}</p>}
      </Block>
      {payload.deployment_instructions && <Block title="Deployment instructions"><p>{payload.deployment_instructions}</p></Block>}
    </div>
  );
}

function Preview({ draft }: { draft: Draft }) {
  const pv = draft.preview;
  if (!pv) return null;
  if (draft.kind === "exception") {
    return (
      <Block title="Representability preview">
        <StatusBadge value={pv.representability} />{" "}
        {pv.reason}
        {pv.native_expiry_supported !== undefined && (
          <div className="text-xs text-slate-500">Native expiry {pv.native_expiry_supported ? "supported" : "not supported"} by the provider.</div>
        )}
      </Block>
    );
  }
  if (draft.kind === "rollout_plan" && pv.capability_notes) {
    return (
      <Block title="Provider capability notes">
        <ul className="list-disc pl-5 text-xs">
          {Object.entries(pv.capability_notes as Record<string, string>).map(([stage, note]) => (
            <li key={stage}><span className="font-medium">{humanize(stage)}:</span> {note}</li>
          ))}
        </ul>
      </Block>
    );
  }
  return <Block title="Preview"><CodeBlock value={pv} maxHeight="12rem" /></Block>;
}

/** The reasons a DRAFT_BLOCKED refusal carries, or its message when it lists none. */
function blockingReasons(error: ApiError): string[] {
  const reasons = (error.details as { blocking_reasons?: unknown } | null | undefined)?.blocking_reasons;
  const listed = Array.isArray(reasons) ? reasons.filter((r): r is string => typeof r === "string" && r.length > 0) : [];
  return listed.length ? listed : [error.message];
}

function confirmSummary(draft: Draft, payload: Record<string, any>): string {
  if (draft.kind === "exception") {
    return `A REQUESTED exception for ${(payload.resource_ids ?? []).join(", ")} at ${payload.scope_id}, expiring ${payload.expires_at}. A security approver decides it.`;
  }
  if (draft.kind === "control") return `Control ${payload.id} revision 1 as a DRAFT.`;
  return `Rollout plan "${payload.title}" for ${payload.control_id} targeting ${payload.target_scope_id} with ${(payload.rings ?? []).length} ring(s).`;
}

/**
 * Review and submit a guided draft. Drafts are unsaved until the person confirms; submission goes through
 * the existing governed service and is refused as DRAFT_STALE when the platform state has moved on.
 */
export function DraftPanel({ draft, previousValues, onPrepareAgain, onDiscard, preparing = false }: {
  draft: Draft;
  /** What the person wrote into the previous version of this draft; kept when it is prepared again. */
  previousValues?: Record<string, string> | null;
  /** Prepares the draft again, handing back what the person has written so far. */
  onPrepareAgain: (values: Record<string, string>) => void;
  onDiscard: () => void;
  preparing?: boolean;
}) {
  const [values, setValues] = useState(() => initialValues(draft, previousValues));
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<DraftSubmitResult | null>(null);

  const fields = fieldsFor(draft);
  const payload = buildPayload(draft, values);
  const missing = missingFields(draft, values, payload);
  const stale = error?.code === "DRAFT_STALE";
  const blocked = error?.code === "DRAFT_BLOCKED" ? blockingReasons(error) : null;
  const canReview = draft.can_submit && missing.length === 0 && !submitting && !result && !stale;

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      setResult(await submitDraft(draft, payload));
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setSubmitting(false);
      setConfirming(false);
    }
  };

  const changed = stale ? ((error?.details as { changed?: string[] } | undefined)?.changed ?? []) : [];

  return (
    <div data-testid="ws-draft" className="mt-4 rounded-lg border-2 border-dashed border-slate-300 p-3 dark:border-slate-600">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-slate-900 dark:text-slate-50">{draft.title}</h3>
        <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-700 dark:bg-slate-800 dark:text-slate-200">
          {result ? "Submitted" : `${KIND_LABEL[draft.kind]} draft · not saved`}
        </span>
      </div>

      {result ? (
        <div className="mt-3" data-testid="ws-draft-created">
          <Notice>
            <div className="font-medium"><span aria-hidden="true">✓ </span>Created {humanize(result.created.type).toLowerCase()} <code>{result.created.id}</code></div>
            <p className="mt-1 text-xs">{result.note}</p>
            {isSafeHref(result.created.href) && (
              <Link href={result.created.href} className="mt-1 inline-block text-sky-700 underline dark:text-sky-300">Open in the Classic Experience</Link>
            )}
          </Notice>
          <div className="mt-2"><Button variant="secondary" onClick={onDiscard} testId="ws-draft-close">Close</Button></div>
        </div>
      ) : (
        <>
          {draft.blocking_reasons.length > 0 && (
            <div className="mt-2" data-testid="ws-draft-blocking">
              <Notice tone="warning">
                <div className="font-medium">This draft cannot be submitted yet</div>
                <ul className="mt-1 list-disc pl-5">{draft.blocking_reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              </Notice>
            </div>
          )}
          {draft.notes.length > 0 && (
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-600 dark:text-slate-300">
              {draft.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          <Preview draft={draft} />
          {draft.kind === "rollout_plan" && <RolloutReview payload={draft.payload} />}

          {fields.length > 0 && (
            <fieldset className="mt-3 space-y-2" disabled={submitting}>
              <legend className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Written by you</legend>
              {fields.map((f) => {
                const set = (v: string) => setValues((cur) => ({ ...cur, [f.key]: v }));
                const testId = `ws-draft-field-${f.key}`;
                if (f.options) return <Select key={f.key} label={f.label} value={values[f.key] ?? ""} onChange={set} options={f.options} testId={testId} />;
                if (f.type === "date") {
                  return (
                    <label key={f.key} className="flex flex-col gap-1 text-xs font-medium text-slate-600 dark:text-slate-300">
                      {f.label}
                      <input type="date" value={values[f.key] ?? ""} onChange={(e) => set(e.target.value)} data-testid={testId}
                             className="rounded border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100" />
                    </label>
                  );
                }
                return (
                  <div key={f.key}>
                    <TextInput label={f.label} value={values[f.key] ?? ""} onChange={set} multiline={f.multiline} testId={testId} />
                    {(f.hint || f.minLength) && (
                      <p className="mt-0.5 text-[11px] text-slate-500 dark:text-slate-400">
                        {f.hint ?? `At least ${f.minLength} characters (${(values[f.key] ?? "").trim().length} so far).`}
                      </p>
                    )}
                  </div>
                );
              })}
            </fieldset>
          )}

          <details className="mt-3 text-xs">
            <summary className="cursor-pointer text-sky-700 dark:text-sky-300">Basis this draft was prepared from</summary>
            <p className="mt-1 text-slate-500 dark:text-slate-400">Submission recomputes this basis; any difference is refused as stale.</p>
            <CodeBlock value={draft.basis} maxHeight="12rem" />
          </details>

          {stale && (
            <div className="mt-3" data-testid="ws-draft-stale">
              <Notice tone="warning">
                <div className="font-medium"><span aria-hidden="true">! </span>This draft is stale</div>
                <p className="mt-1">{error?.message}</p>
                {changed.length > 0 && <p className="mt-1 text-xs">Changed since it was prepared: {changed.join(", ")}</p>}
                <div className="mt-2">
                  <Button onClick={() => onPrepareAgain(values)} disabled={preparing} testId="ws-draft-prepare-again">
                    {preparing ? "Preparing…" : "Prepare again"}
                  </Button>
                </div>
              </Notice>
            </div>
          )}
          {blocked && (
            <div className="mt-3" data-testid="ws-draft-blocked">
              <Notice tone="warning">
                <div className="font-medium"><span aria-hidden="true">! </span>The platform refused this draft</div>
                <ul className="mt-1 list-disc pl-5">{blocked.map((r, i) => <li key={i}>{r}</li>)}</ul>
                <p className="mt-1 text-xs">What you wrote is kept. Change the draft and submit again, or discard it.</p>
              </Notice>
            </div>
          )}
          {!stale && !blocked && <ErrorBox error={error} />}

          {missing.length > 0 && draft.can_submit && (
            <p className="mt-2 text-xs text-slate-600 dark:text-slate-300" data-testid="ws-draft-missing">Still required: {missing.join(", ")}.</p>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <Button onClick={() => setConfirming(true)} disabled={!canReview} testId="ws-draft-submit">Review and submit…</Button>
            <Button variant="secondary" onClick={onDiscard} disabled={submitting} testId="ws-draft-discard">Discard draft</Button>
          </div>
        </>
      )}

      {confirming && (
        <ConfirmDialog title={`Submit ${KIND_LABEL[draft.kind].toLowerCase()}?`} confirmLabel="Submit"
                       onConfirm={submit} onCancel={() => setConfirming(false)} pending={submitting}>
          <p>This creates:</p>
          <p className="mt-1 font-medium">{confirmSummary(draft, payload)}</p>
          <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">Via {draft.submit_via}. Authorization, validation and audit are exactly those of the Classic Experience.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
