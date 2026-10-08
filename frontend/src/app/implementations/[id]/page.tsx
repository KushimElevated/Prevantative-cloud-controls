"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { fmtTime, humanize, shortDigest } from "@/lib/format";
import { CodeBlock, DataTable, ErrorBox, KeyValue, Loading, PageHeader, Section, StatusBadge } from "@/components/ui";

export default function ImplementationPage({ params }: { params: { id: string } }) {
  const { data, error, loading } = useApi<any>(`/implementations/${encodeURIComponent(params.id)}`);
  const caps = useApi<any>("/providers/capabilities");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const latest = data.revisions[data.revisions.length - 1];
  const capability = caps.data?.items.find((c: any) => c.provider === data.provider);
  return (
    <div>
      <PageHeader title={data.name} subtitle={<span>Implementation of <Link className="text-sky-700 underline" href={`/controls/${data.control_id}`}>{data.control_id}</Link> · {data.provider.toUpperCase()} · {humanize(data.mechanism_role)} · {humanize(data.management)}</span>} />
      <Section title="Revisions">
        <DataTable rows={[...data.revisions].reverse()} rowKey={(r: any) => r.id} columns={[
          { key: "r", header: "Revision", render: (r: any) => `r${r.revision}` },
          { key: "s", header: "Status", render: (r: any) => <StatusBadge value={r.status} /> },
          { key: "k", header: "Kind", render: (r: any) => humanize(r.policy_kind) },
          { key: "v", header: "Pinned", render: (r: any) => r.pinned_version ?? "-" },
          { key: "d", header: "Content digest", render: (r: any) => <code className="text-xs">{shortDigest(r.content_digest)}</code> },
          { key: "ver", header: "Verification", render: (r: any) => <StatusBadge value={r.verification.status} /> },
          { key: "val", header: "Validation", render: (r: any) => <StatusBadge value={data.current_validation[r.id]?.outcome} /> },
          { key: "c", header: "Change reason", render: (r: any) => r.change_reason },
        ]} />
      </Section>
      <Section title={`Latest revision r${latest.revision}`}>
        <KeyValue items={[
          ["Source", <code key="s" className="break-all text-xs">{latest.source_ref}</code>],
          ["Parameters", <code key="p" className="text-xs">{JSON.stringify(latest.parameters)}</code>],
          ["Assignment settings", <code key="a" className="text-xs">{JSON.stringify(latest.assignment_settings)}</code>],
          ["Prerequisites", latest.prerequisites.join("; ") || "-"],
          ["Limitations", latest.limitations.join("; ") || "-"],
          ["Verification method", latest.verification.method],
          ["Verified on", latest.verification.verified_on],
          ["Not verified", <ul key="nv" className="list-disc pl-5">{(latest.verification.not_verified ?? []).map((x: string) => <li key={x}>{x}</li>)}</ul>],
        ]} />
        <h3 className="mb-1 mt-4 text-sm font-semibold">Sources</h3>
        <ul className="list-disc pl-5 text-sm">
          {(latest.verification.sources ?? []).map((s: any) => <li key={s.url}><span className="break-all">{s.url}</span>{s.sha256 && <code className="ml-1 text-xs">sha256 {s.sha256.slice(0, 12)}…</code>}<div className="text-xs text-slate-600">{s.establishes}</div></li>)}
        </ul>
        {latest.native_document && (
          <details className="mt-4"><summary className="cursor-pointer text-sm text-sky-700">Native document (pinned copy)</summary><CodeBlock value={latest.native_document} /></details>
        )}
      </Section>
      <Section title="Validation history">
        <DataTable rows={Object.values(data.validation_history).flat() as any[]} rowKey={(v: any) => v.id} empty="Never validated." columns={[
          { key: "t", header: "When", render: (v: any) => fmtTime(v.validated_at) },
          { key: "r", header: "Revision", render: (v: any) => v.implementation_revision_id },
          { key: "o", header: "Outcome", render: (v: any) => <StatusBadge value={v.outcome} /> },
          { key: "e", header: "Evaluator", render: (v: any) => v.evaluator_id ? `${v.evaluator_id} ${v.evaluator_version}` : "none" },
          { key: "i", header: "Integration ready", render: (v: any) => v.integration_ready ? "Yes" : `No: ${v.integration_blockers.join(" ")}` },
        ]} />
      </Section>
      {capability && (
        <Section title="Provider capability contract" description="Declared explicitly; providers are not assumed to share semantics.">
          <CodeBlock value={capability} />
        </Section>
      )}
    </div>
  );
}
