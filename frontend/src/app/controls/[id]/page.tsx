"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { useFeatures } from "@/lib/features";
import { humanize, ratioText } from "@/lib/format";
import { ErrorBox, Loading, Meter, PageHeader, Section, StatTile, StatusBadge } from "@/components/ui";
import { IntentSection } from "@/components/control/IntentSection";
import { BaselineSection, ImplementationsSection } from "@/components/control/ImplementationsSection";
import { AssessmentSection } from "@/components/control/AssessmentSection";
import { ExceptionsSection } from "@/components/control/ExceptionsSection";
import { RolloutSection } from "@/components/control/RolloutSection";
import { AuditSection } from "@/components/control/AuditSection";
import { NextDecision } from "@/components/control/NextDecision";

function Coverage({ detail }: { detail: any }) {
  const c = detail.coverage;
  return (
    <Section title="Coverage for this control" id="coverage" description={c ? c.denominator : "Requires a completed assessment."}>
      {!c ? <p className="text-sm text-slate-500">N/A until an assessment completes.</p> : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Known applicable" value={c.known_applicable_resources} />
          <StatTile label="Verified protected" value={ratioText(c.verified_protected_ratio)} testId="coverage-verified"
                    sub={<Meter numerator={c.verified_protected_ratio.numerator} denominator={c.verified_protected_ratio.denominator} label="Verified protected" />} />
          <StatTile label="Effective exemptions" value={c.effective_exemptions} />
          <StatTile label="Unknown enforcement" value={c.unknown_enforcement} />
          <StatTile label="Unknown applicability" value={c.unknown_applicability} />
          <StatTile label="Remaining non-compliant" value={c.remaining_noncompliant} href={`/assessments/${c.assessment_run_id}?configuration_result=NON_COMPLIANT`} />
        </div>
      )}
    </Section>
  );
}

export default function ControlDetailPage({ params }: { params: { id: string } }) {
  const { data, error, loading, reload } = useApi<any>(`/controls/${encodeURIComponent(params.id)}`);
  const { workspaceEnabled } = useFeatures();
  if (loading && !data) return <Loading what="Loading control" />;
  if (error && !data) return <ErrorBox error={error} />;
  if (!data) return null;
  const rev = data.current_revision;
  return (
    <div>
      <PageHeader title={rev.name}
        subtitle={<span className="flex flex-wrap items-center gap-2">
          <code>{data.control.id}</code>
          <StatusBadge value={rev.status} label={`Revision ${rev.revision}: ${humanize(rev.status)}`} />
          <span>{humanize(rev.severity)} severity</span>
          <span>· {humanize(data.control.origin)}</span>
          <span>· providers {rev.providers.join(", ")}</span>
        </span>}
        actions={workspaceEnabled ? (
          <Link href={`/workspace?control=${encodeURIComponent(data.control.id)}`} data-testid="open-in-workspace"
                className="rounded border border-violet-300 px-3 py-1.5 text-sm font-medium text-violet-800 hover:bg-violet-50 focus:outline-none focus:ring-2 focus:ring-sky-500 dark:border-violet-700 dark:text-violet-200 dark:hover:bg-violet-950">
            Investigate in AI Workspace
          </Link>
        ) : undefined} />
      <NextDecision detail={data} />
      <nav aria-label="Sections" className="mb-4 flex flex-wrap gap-3 text-sm text-sky-700">
        {["intent", "implementations", "baseline", "assessment", "exceptions", "rollout", "coverage", "audit"].map((s) => (
          <a key={s} href={`#${s}`} className="underline">{humanize(s)}</a>
        ))}
      </nav>
      <IntentSection detail={data} reload={reload} />
      <ImplementationsSection detail={data} reload={reload} />
      <BaselineSection detail={data} />
      <AssessmentSection detail={data} reload={reload} />
      <ExceptionsSection detail={data} reload={reload} />
      <RolloutSection detail={data} reload={reload} />
      <Coverage detail={data} />
      <AuditSection key={JSON.stringify(data.pending_decisions)} controlId={data.control.id} />
    </div>
  );
}
