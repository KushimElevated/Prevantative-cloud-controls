"use client";

import { useState } from "react";
import { getToken } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { fmtTime, humanize, leaf, shortDigest } from "@/lib/format";
import { Button, CodeBlock, DataTable, ErrorBox, KeyValue, Loading, Notice, PageHeader, Section, StatusBadge } from "@/components/ui";

export default function BundlePage({ params }: { params: { id: string } }) {
  const { data: b, error, loading } = useApi<any>(`/handoff-bundles/${encodeURIComponent(params.id)}`);
  const [file, setFile] = useState<string>("README.md");
  if (loading && !b) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!b) return null;
  const download = async () => {
    const res = await fetch(`/api/v1/handoff-bundles/${b.id}/download`, { headers: { authorization: `Bearer ${getToken()}` } });
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `ccp-bundle-${b.bundle_digest.split(":")[1].slice(0, 12)}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const paths = Object.keys(b.files).sort();
  return (
    <div>
      <PageHeader title={`Handoff bundle ${b.id}`} subtitle={<span><StatusBadge value={b.kind === "APPROVED_HANDOFF" ? "APPROVED" : "DRAFT"} label={humanize(b.kind)} /> · package {b.package_id}</span>}
                  actions={<Button variant="secondary" onClick={download}>Download deterministic zip</Button>} />
      {b.kind !== "APPROVED_HANDOFF" && <Notice tone="warning">Draft bundle: unapproved and not deployable. Receipts referencing it are rejected.</Notice>}
      <Section title="Bundle">
        <KeyValue items={[
          ["Bundle digest", <code key="d" className="text-xs">{b.bundle_digest}</code>],
          ["Adapter", `${b.adapter} → ${b.export_path}`],
          ["Exported", `${fmtTime(b.exported_at)} by ${b.exported_by}`],
        ]} />
      </Section>
      <Section title="Files" description="Rendered as text; nothing in the bundle is executed or rendered as HTML.">
        <div className="grid gap-4 md:grid-cols-[260px_1fr]">
          <ul className="text-sm">
            {paths.map((p) => (
              <li key={p}>
                <button onClick={() => setFile(p)} className={`w-full truncate rounded px-2 py-1 text-left ${p === file ? "bg-slate-100 font-medium dark:bg-slate-800" : "hover:bg-slate-50 dark:hover:bg-slate-800"}`}>{p}</button>
              </li>
            ))}
          </ul>
          <div>
            <p className="mb-1 text-xs text-slate-500">{file} · {shortDigest(b.file_digests[file])}</p>
            <CodeBlock value={b.files[file] ?? ""} maxHeight="32rem" />
          </div>
        </div>
      </Section>
      <Section title="Delivery targets">
        <DataTable rows={b.delivery_targets} rowKey={(t: any) => t.id} empty="Draft bundles have no delivery targets." columns={[
          { key: "r", header: "Ring", render: (t: any) => humanize(t.ring_stage) },
          { key: "k", header: "Artifact", render: (t: any) => humanize(t.artifact_kind) },
          { key: "n", header: "Native identity", render: (t: any) => <code className="break-all text-xs">{t.native_identity}</code> },
          { key: "d", header: "Expected digest", render: (t: any) => <code className="text-xs">{shortDigest(t.expected_digest)}</code> },
          { key: "s", header: "State", render: (t: any) => <StatusBadge value={t.state} /> },
        ]} />
      </Section>
      <Section title="Receipts (MOCK)" description="Duplicates are idempotent; stale, mismatched and out-of-order receipts are kept as history without changing state.">
        <DataTable rows={b.receipts} rowKey={(r: any) => r.id} empty="No receipts." columns={[
          { key: "k", header: "Receipt", render: (r: any) => <code className="text-xs">{r.receipt_key}</code> },
          { key: "res", header: "Result", render: (r: any) => humanize(r.result) },
          { key: "v", header: "Validation", render: (r: any) => <StatusBadge value={r.validation_status} /> },
          { key: "t", header: "Scope", render: (r: any) => <code className="text-xs">{leaf(r.target_scope_native_id)}</code> },
          { key: "c", header: "Completed", render: (r: any) => fmtTime(r.reported_completed_at) },
          { key: "n", header: "Notes", render: (r: any) => <span className="text-xs">{r.validation_notes.join(" ")}</span> },
        ]} />
      </Section>
      <Section title="Observations (FIXTURE)">
        <DataTable rows={b.observations} rowKey={(o: any) => o.id} empty="No observations." columns={[
          { key: "n", header: "Native identity", render: (o: any) => <code className="text-xs">{leaf(o.native_identity)}</code> },
          { key: "c", header: "Comparison", render: (o: any) => <StatusBadge value={o.comparison} /> },
          { key: "t", header: "Observed", render: (o: any) => fmtTime(o.observed_at) },
          { key: "a", header: "Applied to state", render: (o: any) => (o.applied_to_state ? "Yes" : "No (older)") },
          { key: "d", header: "Differences", render: (o: any) => <span className="text-xs">{o.differences.join("; ") || "-"}</span> },
        ]} />
      </Section>
    </div>
  );
}
