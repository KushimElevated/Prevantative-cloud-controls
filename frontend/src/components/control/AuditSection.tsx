"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { fmtTime, shortDigest } from "@/lib/format";
import { DataTable, ErrorBox, Loading, Section } from "@/components/ui";

export function AuditTable({ rows }: { rows: any[] }) {
  return (
    <DataTable rows={rows} rowKey={(a: any) => String(a.id)} empty="No audit events." columns={[
      { key: "t", header: "When", render: (a: any) => fmtTime(a.occurred_at) },
      { key: "a", header: "Action", render: (a: any) => <code className="text-xs">{a.action}</code> },
      { key: "o", header: "Object", render: (a: any) => <span className="text-xs">{a.object_type} {a.object_id}{a.object_revision ? ` r${a.object_revision}` : ""}</span> },
      { key: "actor", header: "Actor", render: (a: any) => <span className="text-xs">{a.actor_id ?? "system"}<div className="text-slate-500">{a.actor_roles.join(", ")}</div></span> },
      { key: "r", header: "Reason", render: (a: any) => <span className="text-xs">{a.reason ?? "-"}</span> },
      { key: "d", header: "Digests", render: (a: any) => <span className="text-xs">{a.before_digest ? `${shortDigest(a.before_digest)} → ` : ""}{shortDigest(a.after_digest)}</span> },
      { key: "c", header: "Correlation", render: (a: any) => <Link className="text-xs text-sky-700 underline" href={`/audit?correlation_id=${a.correlation_id}`}>{a.correlation_id.slice(0, 8)}</Link> },
    ]} />
  );
}

export function AuditSection({ controlId }: { controlId: string }) {
  const { data, error, loading } = useApi<any>(`/audit?control_id=${encodeURIComponent(controlId)}&limit=25`);
  return (
    <Section title="Audit history" id="audit" description="Append-only through the application; domain changes and their audit events are written atomically."
      actions={<Link className="text-sm text-sky-700 underline" href={`/audit?control_id=${controlId}`}>Full history</Link>}>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && <AuditTable rows={data.items} />}
    </Section>
  );
}
