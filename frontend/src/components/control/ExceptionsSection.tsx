"use client";

import Link from "next/link";
import { useState } from "react";
import { api, hasRole } from "@/lib/api";
import { useApi, useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize, leaf } from "@/lib/format";
import { Button, DataTable, ErrorBox, Section, Select, StatusBadge, TextInput } from "@/components/ui";

export function ExceptionTable({ rows }: { rows: any[] }) {
  return (
    <DataTable rows={rows} rowKey={(e: any) => e.id} empty="No exceptions." columns={[
      { key: "id", header: "Exception", render: (e: any) => <span><Link className="text-sky-700 underline" href={`/exceptions/${e.id}`}>{e.id}</Link><div className="text-xs text-slate-500">rev {e.revision}{e.renews_exception_id ? ` · renews ${e.renews_exception_id}` : ""}</div></span> },
      { key: "app", header: "Application", render: (e: any) => e.application },
      { key: "scope", header: "Scope / resources", render: (e: any) => <span>{e.scope_id}<div className="text-xs text-slate-500">{humanize(e.granularity)}{e.resource_ids.length ? `: ${e.resource_ids.map(leaf).join(", ")}` : ""}{e.principal_patterns.length ? `: ${e.principal_patterns.join(", ")}` : ""}</div></span> },
      { key: "gov", header: "Governance", render: (e: any) => <span><StatusBadge value={e.effective_status} />{e.effective_status !== e.governance_status && <div className="text-xs text-slate-500">stored: {humanize(e.governance_status)}</div>}</span> },
      { key: "native", header: "Native", render: (e: any) => <StatusBadge value={e.native_status} /> },
      { key: "disp", header: "Disposition", render: (e: any) => <StatusBadge value={e.disposition} /> },
      { key: "rep", header: "Representable", render: (e: any) => <StatusBadge value={e.representability} /> },
      { key: "exp", header: "Expires", render: (e: any) => <span>{fmtTime(e.expires_at)}{e.expiring_soon && <div className="text-xs text-amber-700">expiring soon</div>}{e.native_cleanup_pending && <div className="text-xs text-orange-700">native cleanup pending</div>}</span> },
    ]} />
  );
}

function RequestForm({ detail, reload }: { detail: any; reload: () => void }) {
  const cmd = useCommand();
  const provider = detail.current_revision.providers[0];
  const scopes = useApi<any>(`/scopes?provider=${provider}`);
  const [f, setF] = useState({ application: "", team: "", scope_id: "", granularity: "RESOURCE", resource_ids: "",
    principal_patterns: "", business_justification: "", technical_justification: "", risk_owner: "",
    compensating_controls: "", expires_at: "" });
  const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  const submit = () => cmd.run(async () => {
    await api("/exceptions", { method: "POST", body: {
      control_id: detail.control.id, application: f.application, team: f.team, scope_id: f.scope_id,
      granularity: f.granularity, resource_ids: lines(f.resource_ids), principal_patterns: lines(f.principal_patterns),
      business_justification: f.business_justification, technical_justification: f.technical_justification,
      risk_owner: f.risk_owner, compensating_controls: lines(f.compensating_controls),
      expires_at: f.expires_at ? new Date(f.expires_at).toISOString() : "",
    } });
    reload();
  });
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-sm text-sky-700">Request a time-bound exception</summary>
      <p className="mt-1 text-xs text-slate-500">Representability is validated against native mechanisms and inherited policies when you submit.</p>
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        <TextInput label="Application" value={f.application} onChange={(v) => setF({ ...f, application: v })} />
        <TextInput label="Team" value={f.team} onChange={(v) => setF({ ...f, team: v })} />
        <Select label="Exact scope" value={f.scope_id} onChange={(v) => setF({ ...f, scope_id: v })}
                options={[{ value: "", label: "Select scope" }, ...(scopes.data?.items ?? []).map((s: any) => ({ value: s.id, label: s.id }))]} />
        <Select label="Granularity" value={f.granularity} onChange={(v) => setF({ ...f, granularity: v })}
                options={["RESOURCE", "SCOPE", "PRINCIPAL"].map((x) => ({ value: x, label: humanize(x) }))} />
        <TextInput label="Resource ids (one per line)" value={f.resource_ids} onChange={(v) => setF({ ...f, resource_ids: v })} multiline />
        <TextInput label="Principal ARN patterns (AWS, one per line)" value={f.principal_patterns} onChange={(v) => setF({ ...f, principal_patterns: v })} multiline />
        <TextInput label="Business justification" value={f.business_justification} onChange={(v) => setF({ ...f, business_justification: v })} multiline />
        <TextInput label="Technical justification" value={f.technical_justification} onChange={(v) => setF({ ...f, technical_justification: v })} multiline />
        <TextInput label="Compensating controls (one per line)" value={f.compensating_controls} onChange={(v) => setF({ ...f, compensating_controls: v })} multiline />
        <TextInput label="Risk owner" value={f.risk_owner} onChange={(v) => setF({ ...f, risk_owner: v })} />
        <TextInput label="Expires at (YYYY-MM-DDTHH:MMZ)" value={f.expires_at} onChange={(v) => setF({ ...f, expires_at: v })} placeholder="2026-12-31T00:00Z" />
      </div>
      <ErrorBox error={cmd.error} />
      <div className="mt-2"><Button onClick={submit} disabled={cmd.pending}>Submit request</Button></div>
    </details>
  );
}

export function ExceptionsSection({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  return (
    <Section title="Exceptions" id="exceptions"
      description="Governance status and native implementation status are tracked separately; validity is derived from the clock on every read."
      actions={<Link className="text-sm text-sky-700 underline" href={`/exceptions?control_id=${detail.control.id}`}>Review queue</Link>}>
      <ExceptionTable rows={detail.exceptions} />
      {hasRole(user, "EXCEPTION_REQUESTER") && detail.current_revision.exception_eligible && <RequestForm detail={detail} reload={reload} />}
    </Section>
  );
}
