"use client";

import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/hooks";
import { hasRole, qs } from "@/lib/api";
import { useSession } from "@/lib/session";
import { humanize } from "@/lib/format";
import { DataTable, ErrorBox, Loading, PageHeader, Pagination, StatusBadge } from "@/components/ui";

export default function ControlsPage() {
  const [offset, setOffset] = useState(0);
  const { user } = useSession();
  const { data, error, loading } = useApi<any>(`/controls${qs({ limit: 25, offset })}`);
  return (
    <div>
      <PageHeader
        title="Controls"
        subtitle="Security intent is the primary object. Native policies and bindings implement it."
        actions={hasRole(user, "CONTROL_ENGINEER") ? (
          <Link href="/controls/new" className="rounded bg-sky-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-800">
            Propose or register a control
          </Link>
        ) : undefined}
      />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <DataTable
            rows={data.items}
            rowKey={(r: any) => r.id}
            empty="No controls catalogued yet."
            columns={[
              { key: "id", header: "Control", render: (r: any) => (
                <div>
                  <Link href={`/controls/${r.id}`} className="font-medium text-sky-700 underline">{r.id}</Link>
                  <div className="text-xs text-slate-600 dark:text-slate-300">{r.name}</div>
                </div>) },
              { key: "status", header: "Latest revision", render: (r: any) => <span>r{r.latest_revision} <StatusBadge value={r.latest_status} /></span> },
              { key: "sev", header: "Severity", render: (r: any) => humanize(r.severity) },
              { key: "prov", header: "Providers", render: (r: any) => r.providers.join(", ") },
              { key: "origin", header: "Origin", render: (r: any) => humanize(r.origin) },
              { key: "owner", header: "Operational owner", render: (r: any) => r.operational_owner },
              { key: "impl", header: "Implementations", render: (r: any) => r.implementations.map((i: any) => i.name).join("; ") || "-" },
            ]}
          />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}
