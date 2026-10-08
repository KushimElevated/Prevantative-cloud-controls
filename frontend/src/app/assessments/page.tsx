"use client";

import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/hooks";
import { qs } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { DataTable, ErrorBox, Loading, Notice, PageHeader, Pagination, StatusBadge } from "@/components/ui";

export default function AssessmentsPage() {
  const [offset, setOffset] = useState(0);
  const { data, error, loading } = useApi<any>(`/assessments${qs({ limit: 25, offset })}`);
  return (
    <div>
      <PageHeader title="Impact assessments" subtitle="Evaluates current configuration, supplied representative requests and supplied readiness evidence separately." />
      <Notice>Assessments read fixture snapshots only and never modify infrastructure. Start an assessment from a control page.</Notice>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <div className="mt-4">
          <DataTable rows={data.items} rowKey={(r: any) => r.id} empty="No assessments yet." columns={[
            { key: "id", header: "Assessment", render: (r: any) => <Link className="text-sky-700 underline" href={`/assessments/${r.id}`}>{r.id}</Link> },
            { key: "c", header: "Control", render: (r: any) => <Link className="underline" href={`/controls/${r.control_id}`}>{r.control_id}</Link> },
            { key: "s", header: "Status", render: (r: any) => <StatusBadge value={r.status} /> },
            { key: "t", header: "Target", render: (r: any) => r.target_scope_id },
            { key: "e", header: "Evaluator", render: (r: any) => r.evaluator_id ? `${r.evaluator_id} ${r.evaluator_version}` : "none" },
            { key: "cfg", header: "Configuration", render: (r: any) => r.rollup?.configuration ? Object.entries(r.rollup.configuration.counts).map(([k, v]) => `${k.toLowerCase()} ${v}`).join(", ") : r.rollup?.redacted ?? "-" },
            { key: "b", header: "Blockers", render: (r: any) => r.blockers.length },
            { key: "at", header: "Assessed", render: (r: any) => fmtTime(r.assessed_at) },
          ]} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </div>
      )}
    </div>
  );
}
