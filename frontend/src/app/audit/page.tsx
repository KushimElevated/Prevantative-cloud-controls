"use client";

import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useApi } from "@/lib/hooks";
import { qs } from "@/lib/api";
import { AuditTable } from "@/components/control/AuditSection";
import { Button, ErrorBox, Loading, Notice, PageHeader, Pagination, TextInput } from "@/components/ui";

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [offset, setOffset] = useState(0);
  const filters = {
    control_id: params.get("control_id") ?? "",
    object_type: params.get("object_type") ?? "",
    object_id: params.get("object_id") ?? "",
    correlation_id: params.get("correlation_id") ?? "",
    action: params.get("action") ?? "",
  };
  const [draft, setDraft] = useState(filters);
  const { data, error, loading } = useApi<any>(`/audit${qs({ ...filters, limit: 50, offset })}`);
  const apply = () => {
    setOffset(0);
    router.replace(`${pathname}${qs(draft)}`);
  };
  return (
    <div>
      <PageHeader title="Audit history" subtitle="Append-only through the application role." />
      <Notice>Audit rows cannot be updated or deleted by the application database role. This is not proof against a database administrator; externally tamper-resistant retention is a future integration.</Notice>
      <div className="my-4 flex flex-wrap items-end gap-2">
        {(Object.keys(draft) as (keyof typeof draft)[]).map((k) => (
          <TextInput key={k} label={k.replace("_", " ")} value={draft[k]} onChange={(v) => setDraft({ ...draft, [k]: v })} />
        ))}
        <Button onClick={apply}>Filter</Button>
      </div>
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <>
          <AuditTable rows={data.items} />
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </div>
  );
}

export default function AuditPage() {
  return <Suspense fallback={<Loading />}><Inner /></Suspense>;
}
