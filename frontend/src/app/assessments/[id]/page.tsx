"use client";

import Link from "next/link";
import { Suspense, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useApi } from "@/lib/hooks";
import { qs } from "@/lib/api";
import { fmtTime, humanize, leaf } from "@/lib/format";
import { AssessmentSummary } from "@/components/control/AssessmentSection";
import { DataTable, ErrorBox, KeyValue, Loading, PageHeader, Pagination, Section, Select, StatusBadge } from "@/components/ui";

const FILTERS: [string, string[]][] = [
  ["subject_kind", ["RESOURCE", "REQUEST"]],
  ["configuration_result", ["COMPLIANT", "NON_COMPLIANT", "UNKNOWN", "NOT_APPLICABLE"]],
  ["exception_disposition", ["NONE", "PENDING", "APPROVED_UNAPPLIED", "EFFECTIVE", "EXPIRED", "UNSUPPORTED"]],
  ["request_impact", ["PREDICTED_DENIED", "NOT_DENIED_BY_THIS_CONTROL", "UNKNOWN"]],
  ["readiness", ["READY", "BLOCKED", "UNKNOWN"]],
];

function Inner({ id }: { id: string }) {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [offset, setOffset] = useState(0);
  const filters = Object.fromEntries(FILTERS.map(([k]) => [k, params.get(k) ?? ""]));
  const run = useApi<any>(`/assessments/${encodeURIComponent(id)}`);
  const results = useApi<any>(`/assessments/${encodeURIComponent(id)}/results${qs({ ...filters, limit: 50, offset })}`);
  const setFilter = (k: string, v: string) => {
    const sp = new URLSearchParams(params.toString());
    if (v) sp.set(k, v); else sp.delete(k);
    setOffset(0);
    router.replace(`${pathname}?${sp.toString()}`);
  };
  if (run.loading && !run.data) return <Loading />;
  if (run.error) return <ErrorBox error={run.error} />;
  const r = run.data;
  return (
    <div>
      <PageHeader title={`Impact assessment ${r.id}`} subtitle={<span><Link className="text-sky-700 underline" href={`/controls/${r.control_id}`}>{r.control_id}</Link> · target {r.target_scope_id} · {fmtTime(r.assessed_at)} · <StatusBadge value={r.status} /></span>} />
      <Section title="What was evaluated">
        <KeyValue items={[
          ["Control revision", r.control_revision_id],
          ["Implementation revision", <Link key="i" className="underline" href={`/controls/${r.control_id}#implementations`}>{r.implementation_revision_id}</Link>],
          ["Evaluator", r.evaluator_id ? `${r.evaluator_id} v${r.evaluator_version}` : "none (unsupported)"],
          ["Inventory snapshot", r.inventory_snapshot_id],
          ["Request fixtures", r.request_fixture_set_id ?? "none supplied"],
          ["Input digest", <code key="d" className="text-xs">{r.input_digest}</code>],
          ["Result digest", <code key="r" className="text-xs">{r.result_digest}</code>],
        ]} />
      </Section>
      {r.rollup.redacted ? <Section title="Summary"><p className="text-sm">{r.rollup.redacted}</p></Section> : (
        <Section title="Summary"><AssessmentSummary run={r} /></Section>
      )}
      <Section title="Results" description="Filter by any dimension; summary counts above link here.">
        <div className="mb-3 flex flex-wrap gap-2">
          {FILTERS.map(([k, opts]) => (
            <Select key={k} label={humanize(k)} value={filters[k]} onChange={(v) => setFilter(k, v)}
                    options={[{ value: "", label: "Any" }, ...opts.map((o) => ({ value: o, label: humanize(o) }))]} />
          ))}
        </div>
        <ErrorBox error={results.error} />
        {results.loading && !results.data ? <Loading /> : results.data && (
          <>
            <p className="mb-2 text-xs text-slate-500">{results.data.total} matching rows (scoped to your authorised scopes).</p>
            <DataTable rows={results.data.items} rowKey={(x: any) => x.id} columns={[
              { key: "k", header: "Kind", render: (x: any) => humanize(x.subject_kind) },
              { key: "s", header: "Subject", render: (x: any) => <span><span className="font-medium">{x.subject_name}</span><div className="break-all text-xs text-slate-500">{leaf(x.subject_id)}</div></span> },
              { key: "sc", header: "Scope / app", render: (x: any) => <span>{x.scope_id}<div className="text-xs text-slate-500">{x.application ?? "no application"} · {x.owner ?? "no owner"}</div></span> },
              { key: "c", header: "Configuration", render: (x: any) => <StatusBadge value={x.configuration_result} /> },
              { key: "e", header: "Exception", render: (x: any) => x.exception_id ? <Link href={`/exceptions/${x.exception_id}`}><StatusBadge value={x.exception_disposition} /></Link> : <StatusBadge value={x.exception_disposition} /> },
              { key: "i", header: "Request impact", render: (x: any) => <StatusBadge value={x.request_impact} /> },
              { key: "r", header: "Readiness", render: (x: any) => <StatusBadge value={x.readiness} /> },
              { key: "b", header: "Baseline", render: (x: any) => humanize(x.baseline?.summary) },
              { key: "why", header: "Reasons", render: (x: any) => <ul className="list-disc pl-4 text-xs">{x.reasons.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul> },
            ]} />
            <Pagination total={results.data.total} limit={results.data.limit} offset={results.data.offset} onChange={setOffset} />
          </>
        )}
      </Section>
      {!r.rollup.redacted && (
        <Section title="Limitations and assumptions">
          <ul className="list-disc pl-5 text-sm">{r.limitations.map((l: string) => <li key={l}>{l}</li>)}</ul>
          <ul className="mt-2 list-disc pl-5 text-sm text-slate-600">{r.assumptions.map((l: string) => <li key={l}>{l}</li>)}</ul>
        </Section>
      )}
    </div>
  );
}

export default function AssessmentPage({ params }: { params: { id: string } }) {
  return <Suspense fallback={<Loading />}><Inner id={params.id} /></Suspense>;
}
