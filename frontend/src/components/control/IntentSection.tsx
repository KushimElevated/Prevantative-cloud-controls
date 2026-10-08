"use client";

import { api, hasRole } from "@/lib/api";
import { useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize, shortDigest } from "@/lib/format";
import { Button, DataTable, ErrorBox, KeyValue, Section, StatusBadge } from "@/components/ui";

export function IntentSection({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const rev = detail.current_revision;
  const canAuthor = hasRole(user, "CONTROL_ENGINEER");

  const submit = () => cmd.run(async () => {
    await api(`/control-revisions/${rev.id}/submit`, { method: "POST", body: { expected_lock_version: rev.lock_version } });
    reload();
  });
  const newRevision = () => cmd.run(async () => {
    const reason = window.prompt("Change reason for the new revision");
    if (!reason) return;
    await api(`/controls/${detail.control.id}/revisions`, { method: "POST", body: { change_reason: reason } });
    reload();
  });

  return (
    <Section title="Security intent" id="intent"
      description="Provider-independent statement of what must become impossible, within which scope, and its limits."
      actions={canAuthor ? (
        <>
          {rev.status === "DRAFT" && <Button onClick={submit} disabled={cmd.pending} testId="submit-control">Submit revision {rev.revision} for review</Button>}
          {rev.status !== "DRAFT" && rev.status !== "RETIRED" && <Button variant="secondary" onClick={newRevision} disabled={cmd.pending}>New revision</Button>}
        </>
      ) : undefined}>
      <ErrorBox error={cmd.error} />
      <KeyValue items={[
        ["Security objective", <strong key="o">{rev.security_objective}</strong>],
        ["Prevention boundary", rev.prevention_boundary],
        ["Limitations", <ul key="l" className="list-disc pl-5">{rev.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>],
        ["Applicability", rev.applicability_criteria],
        ["Rationale", rev.rationale],
        ["Resource types", rev.resource_types.join(", ")],
        ["Severity", humanize(rev.severity)],
        ["Security owner", rev.security_owner],
        ["Engineering owner", rev.engineering_owner],
        ["Operational owner", `${detail.control.operational_owner} (${humanize(detail.control.origin)})`],
        ["Exception eligible", rev.exception_eligible ? "Yes" : "No"],
        ["Framework references", rev.framework_refs.join("; ") || "-"],
        ["Source evidence", <ul key="e" className="list-disc pl-5">{rev.source_evidence.map((e: any) => (
          <li key={e.reference}><span className="font-medium">{humanize(e.kind)}</span>: {e.summary} <span className="text-xs text-slate-500">({e.reference})</span></li>
        ))}</ul>],
      ]} />
      <h3 className="mb-2 mt-5 text-sm font-semibold">Revision history</h3>
      <DataTable rows={[...detail.revisions].reverse()} rowKey={(r: any) => r.id} columns={[
        { key: "r", header: "Revision", render: (r: any) => r.revision },
        { key: "s", header: "Status", render: (r: any) => <StatusBadge value={r.status} /> },
        { key: "reason", header: "Change reason", render: (r: any) => r.change_reason },
        { key: "d", header: "Content digest", render: (r: any) => <code className="text-xs">{shortDigest(r.content_digest)}</code> },
        { key: "by", header: "Author", render: (r: any) => r.created_by },
        { key: "sub", header: "Submitted", render: (r: any) => fmtTime(r.submitted_at) },
        { key: "app", header: "Approved", render: (r: any) => fmtTime(r.approved_at) },
      ]} />
    </Section>
  );
}
