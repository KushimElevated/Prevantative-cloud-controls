"use client";

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useApi } from "@/lib/hooks";
import { qs } from "@/lib/api";
import { humanize } from "@/lib/format";
import { ExceptionTable } from "@/components/control/ExceptionsSection";
import { ErrorBox, Loading, PageHeader, Pagination, Select } from "@/components/ui";

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [offset, setOffset] = useState(0);
  const f = {
    control_id: params.get("control_id") ?? "",
    effective_status: params.get("effective_status") ?? "",
    disposition: params.get("disposition") ?? "",
    expiring: params.get("expiring") ?? "",
    native_cleanup_pending: params.get("native_cleanup_pending") ?? "",
  };
  const { data, error, loading } = useApi<any>(`/exceptions${qs({ ...f, limit: 50, offset })}`);
  const set = (k: string, v: string) => {
    const sp = new URLSearchParams(params.toString());
    if (v) sp.set(k, v); else sp.delete(k);
    setOffset(0);
    router.replace(`${pathname}?${sp.toString()}`);
  };
  return (
    <div>
      <PageHeader title="Exceptions"
        subtitle="Time-bound, scoped, justified exceptions. Approval never overrides an inherited native deny and never counts as compliance." />
      <div className="mb-4 flex flex-wrap gap-2">
        <Select label="Effective status" value={f.effective_status} onChange={(v) => set("effective_status", v)}
                options={[{ value: "", label: "Any" }, ...["REQUESTED", "SECURITY_REVIEW", "APPROVED", "REJECTED", "EXPIRED", "REVOKED"].map((x) => ({ value: x, label: humanize(x) }))]} />
        <Select label="Disposition" value={f.disposition} onChange={(v) => set("disposition", v)}
                options={[{ value: "", label: "Any" }, ...["PENDING", "APPROVED_UNAPPLIED", "EFFECTIVE", "EXPIRED", "UNSUPPORTED", "NONE"].map((x) => ({ value: x, label: humanize(x) }))]} />
        <Select label="Expiring soon" value={f.expiring} onChange={(v) => set("expiring", v)}
                options={[{ value: "", label: "Any" }, { value: "true", label: "Only expiring" }]} />
        <Select label="Native cleanup" value={f.native_cleanup_pending} onChange={(v) => set("native_cleanup_pending", v)}
                options={[{ value: "", label: "Any" }, { value: "true", label: "Cleanup/removal pending" }]} />
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <ExceptionTable rows={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

export default function ExceptionsPage() {
  return <Suspense fallback={<Loading />}><Inner /></Suspense>;
}
