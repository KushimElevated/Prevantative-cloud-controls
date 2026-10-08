"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, hasRole } from "@/lib/api";
import { useApi, useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize } from "@/lib/format";
import { Button, CodeBlock, DataTable, ErrorBox, KeyValue, Loading, Notice, PageHeader, Section, StatusBadge, TextInput } from "@/components/ui";

export default function ExceptionPage({ params }: { params: { id: string } }) {
  const { user } = useSession();
  const router = useRouter();
  const { data: e, error, loading, reload } = useApi<any>(`/exceptions/${encodeURIComponent(params.id)}`);
  const cmd = useCommand();
  const [rationale, setRationale] = useState("");
  const [renewal, setRenewal] = useState({ expires_at: "", business_justification: "", technical_justification: "", compensating_controls: "" });
  if (loading && !e) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!e) return null;
  const isSec = hasRole(user, "SECURITY_APPROVER");
  const post = (path: string, body: any) => cmd.run(async () => {
    const out = await api<any>(`/exceptions/${e.id}/${path}`, { method: "POST", body });
    if (path === "renew") router.push(`/exceptions/${out.id}`); else reload();
  });
  return (
    <div>
      <PageHeader title={`Exception ${e.id}`} subtitle={<span><Link className="text-sky-700 underline" href={`/controls/${e.control_id}`}>{e.control_id}</Link> · {e.application} · revision {e.revision}</span>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <StatusBadge value={e.effective_status} label={`Governance: ${humanize(e.effective_status)}`} />
        <StatusBadge value={e.native_status} label={`Native: ${humanize(e.native_status)}`} />
        <StatusBadge value={e.disposition} label={`Disposition: ${humanize(e.disposition)}`} />
        <StatusBadge value={e.representability} />
      </div>
      {e.effective_status !== e.governance_status && (
        <Notice tone="warning">Stored status is {humanize(e.governance_status)} but validity is derived from the clock: this exception is {humanize(e.effective_status)} now, even if reconciliation has not run.</Notice>
      )}
      {e.native_cleanup_pending && <Notice tone="warning">Native cleanup/removal is pending. The platform does not claim the cloud representation was removed.</Notice>}
      <Section title="Request">
        <KeyValue items={[
          ["Requester", e.requester_id], ["Team", e.team], ["Risk owner", e.risk_owner],
          ["Exact scope", `${e.scope_id} (${humanize(e.granularity)})`],
          ["Resources", e.resource_ids.join(", ") || "-"], ["Principal patterns", e.principal_patterns.join(", ") || "-"],
          ["Business justification", e.business_justification], ["Technical justification", e.technical_justification],
          ["Compensating controls", e.compensating_controls.join("; ")],
          ["Validity", `${fmtTime(e.valid_from)} → ${fmtTime(e.expires_at)} (${e.days_until_expiry} days)`],
          ["Representability", e.representability_reason],
          ["Native expiry", e.native_expiry_supported ? `Supported${e.native_expires_at ? ` (observed expiresOn ${fmtTime(e.native_expires_at)})` : ""}` : "Not supported: a removal handoff is required at expiry"],
          ["Native observed", e.native_observed_at ? `${fmtTime(e.native_observed_at)} (${e.native_provenance})` : "Never"],
          ["Content digest", <code key="d" className="text-xs">{e.content_digest}</code>],
        ]} />
        {e.native_representation && <details className="mt-3"><summary className="cursor-pointer text-sm text-sky-700">Native representation</summary><CodeBlock value={e.native_representation} /></details>}
      </Section>
      <Section title="Decisions" description="Every decision is preserved, bound to the digest that was reviewed.">
        <DataTable rows={e.decisions} rowKey={(d: any) => d.id} empty="No decisions yet." columns={[
          { key: "d", header: "Decision", render: (d: any) => <StatusBadge value={d.decision === "APPROVE" ? "APPROVED" : d.decision === "REJECT" ? "REJECTED" : "REVOKED"} label={d.decision} /> },
          { key: "a", header: "By", render: (d: any) => `${d.actor_id} (${humanize(d.role)})` },
          { key: "r", header: "Rationale", render: (d: any) => d.rationale },
          { key: "t", header: "When", render: (d: any) => fmtTime(d.decided_at) },
        ]} />
        <ErrorBox error={cmd.error} />
        {isSec && (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <TextInput label="Rationale" value={rationale} onChange={setRationale} />
            {e.effective_status === "REQUESTED" && <Button variant="secondary" onClick={() => post("start-review", { expected_lock_version: e.lock_version })} disabled={cmd.pending}>Start security review</Button>}
            {["REQUESTED", "SECURITY_REVIEW"].includes(e.effective_status) && (
              <>
                <Button onClick={() => post("decision", { decision: "APPROVE", rationale, expected_lock_version: e.lock_version })} disabled={cmd.pending}>Approve (accept risk)</Button>
                <Button variant="danger" onClick={() => post("decision", { decision: "REJECT", rationale, expected_lock_version: e.lock_version })} disabled={cmd.pending}>Reject</Button>
              </>
            )}
            {e.effective_status === "APPROVED" && <Button variant="danger" onClick={() => post("revoke", { reason: rationale, expected_lock_version: e.lock_version })} disabled={cmd.pending}>Revoke</Button>}
          </div>
        )}
      </Section>
      {hasRole(user, "EXCEPTION_REQUESTER") && ["APPROVED", "EXPIRED"].includes(e.effective_status) && (
        <Section title="Request renewal" description="Renewal creates a new reviewed revision; the current expiration is preserved.">
          <div className="grid gap-2 md:grid-cols-2">
            <TextInput label="New expiry (YYYY-MM-DDTHH:MMZ)" value={renewal.expires_at} onChange={(v) => setRenewal({ ...renewal, expires_at: v })} />
            <TextInput label="Compensating controls (one per line)" value={renewal.compensating_controls} onChange={(v) => setRenewal({ ...renewal, compensating_controls: v })} multiline />
            <TextInput label="Business justification" value={renewal.business_justification} onChange={(v) => setRenewal({ ...renewal, business_justification: v })} multiline />
            <TextInput label="Technical justification" value={renewal.technical_justification} onChange={(v) => setRenewal({ ...renewal, technical_justification: v })} multiline />
          </div>
          <div className="mt-2"><Button onClick={() => post("renew", {
            expires_at: renewal.expires_at ? new Date(renewal.expires_at).toISOString() : "",
            business_justification: renewal.business_justification, technical_justification: renewal.technical_justification,
            compensating_controls: renewal.compensating_controls.split("\n").map((x) => x.trim()).filter(Boolean) })} disabled={cmd.pending}>Request renewal</Button></div>
        </Section>
      )}
      <Section title="Lineage">
        <DataTable rows={e.lineage} rowKey={(x: any) => x.id} columns={[
          { key: "r", header: "Revision", render: (x: any) => <Link className="text-sky-700 underline" href={`/exceptions/${x.id}`}>r{x.revision} {x.id}</Link> },
          { key: "s", header: "Status", render: (x: any) => <StatusBadge value={x.effective_status} /> },
          { key: "e", header: "Expires", render: (x: any) => fmtTime(x.expires_at) },
        ]} />
      </Section>
    </div>
  );
}
