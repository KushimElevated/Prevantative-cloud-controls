"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { api, hasRole } from "@/lib/api";
import { useApi, useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize, leaf } from "@/lib/format";
import { Button, DataTable, ErrorBox, Notice, Section, Select, StatTile, StatusBadge, TextInput } from "@/components/ui";

export function AssessmentSummary({ run }: { run: any }) {
  if (run.status !== "COMPLETED") {
    return (
      <Notice tone="warning">
        <StatusBadge value={run.status} /> {run.rollup.reason ?? run.disclosure}
      </Notice>
    );
  }
  const r = run.rollup;
  const cfg = r.configuration.counts;
  const base = `/assessments/${run.id}`;
  const req = r.request_impact;
  return (
    <div>
      <p className="mb-3 text-xs text-slate-600 dark:text-slate-300">{run.disclosure}</p>
      <h4 className="mb-2 text-sm font-semibold">1. Current configuration <span className="font-normal text-slate-500">({r.configuration.total_resources_evaluated} resources; counts are mutually exclusive and sum to the total)</span></h4>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4" data-testid="config-counts">
        {(["COMPLIANT", "NON_COMPLIANT", "UNKNOWN", "NOT_APPLICABLE"] as const).map((k) => (
          <StatTile key={k} label={humanize(k)} value={cfg[k]} href={`${base}?configuration_result=${k}`} testId={`count-${k}`} />
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-600">
        Exceptions (reported separately; never counted as compliant):{" "}
        {Object.entries(r.exceptions.counts).filter(([, v]) => (v as number) > 0).map(([k, v]) => (
          <Link key={k} href={`${base}?exception_disposition=${k}`} className="mr-2 underline">{humanize(k)} {String(v)}</Link>
        ))}
      </p>
      <h4 className="mb-2 mt-4 text-sm font-semibold">2. Request impact <span className="font-normal text-slate-500">(supplied representative requests only)</span></h4>
      {req.evidence_present ? (
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3" data-testid="request-counts">
          {Object.entries(req.counts).map(([k, v]) => (
            <StatTile key={k} label={humanize(k)} value={String(v)} href={`${base}?subject_kind=REQUEST&request_impact=${k}`} />
          ))}
        </div>
      ) : <Notice tone="warning">No request evidence: potentially blocked operations are <strong>unknown</strong> (not zero).</Notice>}
      <p className="mt-1 text-xs text-slate-500">{req.note}</p>
      <h4 className="mb-2 mt-4 text-sm font-semibold">3. Operational readiness</h4>
      <DataTable rows={Object.entries(r.readiness.by_application) as [string, string][]} rowKey={([k]) => k} columns={[
        { key: "a", header: "Application", render: ([k]) => k },
        { key: "s", header: "Readiness", render: ([, v]) => <StatusBadge value={v} /> },
      ]} />
      <div className="mt-3 grid gap-2 text-xs text-slate-600 md:grid-cols-3">
        <div><span className="font-semibold">Confidence (configuration):</span> {run.confidence.configuration.category} - {run.confidence.configuration.reasons.join(" ")}</div>
        <div><span className="font-semibold">Confidence (requests):</span> {run.confidence.request_impact.category} - {run.confidence.request_impact.reasons.join(" ")}</div>
        <div><span className="font-semibold">Confidence (readiness):</span> {run.confidence.readiness.category} - {run.confidence.readiness.reasons.join(" ")}</div>
      </div>
      <p className="mt-2 text-xs text-slate-600">Baseline vs proposed: {Object.entries(r.baseline_vs_proposed.baseline_coverage_counts).map(([k, v]) => `${humanize(k)} ${v}`).join(", ")}; resources that would newly receive preventive coverage: {r.baseline_vs_proposed.newly_preventive_resources}.</p>
    </div>
  );
}

function EvidenceForm({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const provider = detail.current_revision.providers[0];
  const prereqs: any[] = detail.implementations.find((i: any) => i.implementation.provider === provider)?.prerequisites ?? [];
  const scopes = useApi<any>(`/scopes?provider=${provider}`);
  const [f, setF] = useState({ application: "", scope_id: "", prerequisite: prereqs[0]?.id ?? "", status: "SATISFIED", summary: "", evidence_ref: "" });
  if (!user || !(hasRole(user, "CONTROL_ENGINEER") || hasRole(user, "EXCEPTION_REQUESTER") || hasRole(user, "CLOUD_ENGINEER"))) return null;
  const save = () => cmd.run(async () => {
    await api("/readiness-evidence", { method: "POST", body: { control_id: detail.control.id, ...f, evidence_ref: f.evidence_ref || null } });
    setF({ ...f, summary: "", evidence_ref: "" });
    reload();
  });
  const scopeOptions = [{ value: "", label: "Select scope" }, ...(scopes.data?.items ?? []).map((s: any) => ({ value: s.id, label: `${s.id} (${s.scope_type})` }))];
  return (
    <details className="mt-4" data-testid="evidence-form">
      <summary className="cursor-pointer text-sm text-sky-700">Record readiness evidence</summary>
      <p className="mt-1 text-xs text-slate-500">Evidence is attested by the named team. The platform does not run network tests.</p>
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        <TextInput label="Application" value={f.application} onChange={(v) => setF({ ...f, application: v })} testId="ev-app" />
        <Select label="Scope" value={f.scope_id} onChange={(v) => setF({ ...f, scope_id: v })} options={scopeOptions} testId="ev-scope" />
        <Select label="Prerequisite" value={f.prerequisite} onChange={(v) => setF({ ...f, prerequisite: v })}
                options={prereqs.map((p) => ({ value: p.id, label: p.label }))} testId="ev-prereq" />
        <Select label="Status" value={f.status} onChange={(v) => setF({ ...f, status: v })}
                options={["SATISFIED", "NOT_SATISFIED", "UNKNOWN"].map((x) => ({ value: x, label: humanize(x) }))} testId="ev-status" />
        <TextInput label="Summary" value={f.summary} onChange={(v) => setF({ ...f, summary: v })} testId="ev-summary" />
        <TextInput label="Evidence reference (ticket/link)" value={f.evidence_ref} onChange={(v) => setF({ ...f, evidence_ref: v })} />
      </div>
      <ErrorBox error={cmd.error} />
      <div className="mt-2"><Button onClick={save} disabled={cmd.pending} testId="ev-save">Save evidence</Button></div>
    </details>
  );
}

export function AssessmentSection({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const latest = detail.assessments[0];
  const implRevs = useMemo(() => detail.implementations.map((i: any) => i.revisions[i.revisions.length - 1]), [detail]);
  const provider = detail.current_revision.providers[0];
  const scopes = useApi<any>(`/scopes?provider=${provider}`);
  const roots = (scopes.data?.items ?? []).filter((s: any) => !s.parent_id);
  const [implRev, setImplRev] = useState<string>(implRevs[0]?.id ?? "");
  const [target, setTarget] = useState<string>("");
  const effectiveTarget = target || roots[0]?.id || "";
  const run = () => cmd.run(async () => {
    await api("/assessments", { method: "POST", body: { implementation_revision_id: implRev || implRevs[0]?.id, target_scope_id: effectiveTarget } });
    reload();
  });
  const route = () => cmd.run(async () => {
    const out = await api<any>(`/assessments/${latest.id}/route-blockers`, { method: "POST" });
    window.alert(`${out.created.length} new work reference(s) created for owning teams.`);
  });
  const canRun = hasRole(user, "CONTROL_ENGINEER");
  return (
    <Section title="Impact assessment" id="assessment"
      description="Deterministic, versioned evaluation of fixtures. Never modifies infrastructure."
      actions={canRun && implRevs.length > 0 ? (
        <div className="flex flex-wrap items-end gap-2">
          <Select label="Implementation revision" value={implRev || implRevs[0]?.id} onChange={setImplRev}
                  options={implRevs.map((r: any) => ({ value: r.id, label: `${r.id} (r${r.revision})` }))} />
          <Select label="Target scope" value={effectiveTarget} onChange={setTarget}
                  options={(scopes.data?.items ?? []).map((s: any) => ({ value: s.id, label: s.id }))} testId="assess-target" />
          <Button onClick={run} disabled={cmd.pending} testId="run-assessment">{cmd.pending ? "Assessing…" : "Run assessment"}</Button>
        </div>
      ) : undefined}>
      <ErrorBox error={cmd.error} />
      {!latest ? <p className="text-sm text-slate-500">No assessment yet.</p> : (
        <>
          <p className="mb-2 text-sm">
            Latest: <Link className="text-sky-700 underline" href={`/assessments/${latest.id}`}>{latest.id}</Link> · {fmtTime(latest.assessed_at)} ·
            target {latest.target_scope_id} · <StatusBadge value={latest.status} />
          </p>
          <AssessmentSummary run={latest} />
          <h4 className="mb-2 mt-5 text-sm font-semibold">Readiness blockers ({latest.blockers.length})</h4>
          <DataTable rows={latest.blockers.slice(0, 25)} rowKey={(b: any) => b.id} empty="No blockers." columns={[
            { key: "k", header: "Kind", render: (b: any) => humanize(b.kind) },
            { key: "s", header: "Subject", render: (b: any) => <code className="text-xs">{leaf(b.subject_id)}</code> },
            { key: "sc", header: "Scope", render: (b: any) => b.scope_id },
            { key: "m", header: "Why", render: (b: any) => b.message },
            { key: "r", header: "Resolution", render: (b: any) => b.resolution },
            { key: "o", header: "Owner", render: (b: any) => b.owner_team },
          ]} />
          {latest.blockers.length > 25 && <p className="text-xs text-slate-500">Showing 25 of {latest.blockers.length}; see the assessment page for all.</p>}
          <div className="mt-2 flex gap-2">
            {canRun && latest.blockers.length > 0 && <Button variant="secondary" onClick={route} disabled={cmd.pending}>Route blockers to owners (work references)</Button>}
          </div>
          <h4 className="mb-1 mt-4 text-sm font-semibold">Next steps</h4>
          <ul className="list-disc pl-5 text-sm">{latest.next_steps.map((s: string) => <li key={s}>{s}</li>)}</ul>
        </>
      )}
      <EvidenceForm detail={detail} reload={reload} />
    </Section>
  );
}
