"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { ErrorBox, Loading, PageHeader, Section } from "@/components/ui";

export default function SimulationPage() {
  const { data, error, loading } = useApi<any>("/simulation");
  return (
    <div>
      <PageHeader title="Simulation" subtitle={<span>This feature is called <strong>Impact Assessment</strong>. <Link className="text-sky-700 underline" href="/assessments">Go to assessments</Link>.</span>} />
      <ErrorBox error={error} />
      {loading && !data ? <Loading /> : data && (
        <Section title="Exactly what the engine evaluates">
          <h3 className="text-sm font-semibold">Evaluated</h3>
          <ul className="mb-3 list-disc pl-5 text-sm">{data.what_is_evaluated.map((x: string) => <li key={x}>{x}</li>)}</ul>
          <h3 className="text-sm font-semibold">Not evaluated</h3>
          <ul className="mb-3 list-disc pl-5 text-sm">{data.what_is_not_evaluated.map((x: string) => <li key={x}>{x}</li>)}</ul>
          <p className="text-sm">{data.infrastructure_changes}</p>
        </Section>
      )}
    </div>
  );
}
