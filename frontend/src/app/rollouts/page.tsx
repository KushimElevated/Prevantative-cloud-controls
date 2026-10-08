"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { fmtTime, humanize, leaf, shortDigest } from "@/lib/format";
import { DataTable, ErrorBox, Loading, PageHeader, Section, StatusBadge } from "@/components/ui";

export default function RolloutsPage() {
  const plans = useApi<any>("/rollout-plans");
  const deliveries = useApi<any>("/deliveries");
  return (
    <div>
      <PageHeader title="Rollouts and handoffs" subtitle="Rollout progression, package approval and delivery state are tracked separately." />
      <ErrorBox error={plans.error ?? deliveries.error} />
      {plans.loading && !plans.data ? <Loading /> : plans.data && (
        <Section title="Rollout plans">
          <DataTable rows={plans.data.items} rowKey={(p: any) => p.id} empty="No rollout plans yet. Create one from a control page." columns={[
            { key: "p", header: "Plan", render: (p: any) => <span><Link className="text-sky-700 underline" href={`/controls/${p.control_id}#rollout`}>{p.title}</Link><div className="text-xs text-slate-500">{p.id}</div></span> },
            { key: "c", header: "Control", render: (p: any) => p.control_id },
            { key: "s", header: "Stage", render: (p: any) => humanize(p.stage) },
            { key: "st", header: "State", render: (p: any) => <StatusBadge value={p.state} /> },
            { key: "r", header: "Rings", render: (p: any) => p.rings.map((r: any) => humanize(r.stage)).join(" → ") },
            { key: "pk", header: "Latest package", render: (p: any) => p.packages[0] ? <span>r{p.packages[0].package_revision} <StatusBadge value={p.packages[0].status} /> <code className="text-xs">{shortDigest(p.packages[0].manifest_digest)}</code></span> : "-" },
          ]} />
        </Section>
      )}
      {deliveries.data && (
        <Section title="Delivery targets" description="All receipts and observations in this MVP are MOCK/FIXTURE data.">
          <DataTable rows={deliveries.data.items} rowKey={(t: any) => t.id} empty="Nothing exported yet." columns={[
            { key: "b", header: "Bundle", render: (t: any) => <Link className="text-sky-700 underline" href={`/bundles/${t.bundle_id}`}>{t.bundle_id}</Link> },
            { key: "c", header: "Control", render: (t: any) => t.control_id },
            { key: "r", header: "Ring", render: (t: any) => humanize(t.ring_stage) },
            { key: "k", header: "Artifact", render: (t: any) => humanize(t.artifact_kind) },
            { key: "n", header: "Native identity", render: (t: any) => <code className="text-xs">{leaf(t.native_identity)}</code> },
            { key: "s", header: "State", render: (t: any) => <StatusBadge value={t.state} /> },
            { key: "t", header: "Changed", render: (t: any) => fmtTime(t.state_changed_at) },
          ]} />
        </Section>
      )}
    </div>
  );
}
