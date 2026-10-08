"use client";

import Link from "next/link";
import { useApi } from "@/lib/hooks";
import { humanize, ratioText } from "@/lib/format";
import { DataTable, Empty, ErrorBox, Loading, Meter, PageHeader, Section, StatTile, StatusBadge } from "@/components/ui";

export default function DashboardPage() {
  const { data, error, loading } = useApi<any>("/dashboard/metrics");
  if (loading && !data) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const pairs = data.coverage_pairs;
  const exc = data.exceptions;
  return (
    <div>
      <PageHeader title="Dashboard" subtitle={<>Decision metrics from persisted data ({data.scope_filter}). Every ratio names its denominator; empty denominators show N/A.</>} />

      <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Verified protected (control-resource pairs)" testId="tile-verified"
                  value={ratioText(pairs.verified_protected_ratio)}
                  sub={<><Meter numerator={pairs.verified_protected_ratio.numerator} denominator={pairs.verified_protected_ratio.denominator} label="Verified protected pairs" /><span>{pairs.denominator}</span></>} />
        <StatTile label="Remaining non-compliance (pairs)" value={pairs.remaining_noncompliant ?? 0}
                  sub="Applicable pairs whose configuration is non-compliant; exceptions do not reduce this." href="/assessments" />
        <StatTile label="Effective exemptions" value={pairs.effective_exemptions ?? 0}
                  sub="Shown separately; never counted as protected." href="/exceptions?disposition=EFFECTIVE" />
        <StatTile label="Unknown evidence" value={(data.evidence.unknown_configuration_pairs ?? 0) + (data.evidence.stale_readiness_evidence_items ?? 0)}
                  sub={`${data.evidence.unknown_configuration_pairs ?? 0} unknown configuration pairs, ${data.evidence.stale_readiness_evidence_items} stale evidence items`} />
        <StatTile label="Unknown enforcement (pairs)" value={pairs.unknown_enforcement ?? 0}
                  sub="Covered by a binding without a fresh matching observation." />
        <StatTile label="Unknown applicability (pairs)" value={pairs.unknown_applicability ?? 0} />
        <StatTile label="Missing owners (pairs)" value={data.evidence.missing_owner_pairs ?? 0} />
        <StatTile label="Controls catalogued" value={data.controls.total_catalogued} sub={data.controls.note} href="/controls" />
      </div>

      <Section title="Controls by target scope" description="Proposed = latest revision draft or in review. Observed = rollout reached observation. Verified = at least one fresh, matching observation after delivery.">
        <DataTable
          rows={Object.entries(data.controls.lifecycle_by_target_scope) as [string, any][]}
          rowKey={([k]) => k}
          columns={[
            { key: "scope", header: "Target scope", render: ([k]) => k },
            { key: "proposed", header: "Proposed", render: ([, v]) => v.proposed ?? 0 },
            { key: "approved", header: "Approved", render: ([, v]) => v.approved ?? 0 },
            { key: "observed", header: "Observed", render: ([, v]) => v.observed ?? 0 },
            { key: "verified", header: "Verified", render: ([, v]) => v.verified ?? 0 },
          ]}
        />
      </Section>

      <Section title="Coverage per control" description="Denominator: known-applicable resources in the control's latest completed assessment. Exceptions stay in the denominator.">
        <DataTable
          rows={data.controls.per_control}
          rowKey={(r: any) => r.control_id}
          columns={[
            { key: "c", header: "Control", render: (r: any) => <Link className="text-sky-700 underline" href={r.links.control}>{r.control_id}</Link> },
            { key: "s", header: "Status", render: (r: any) => <StatusBadge value={r.lifecycle.latest_status} /> },
            { key: "o", header: "Origin", render: (r: any) => humanize(r.origin) },
            { key: "ka", header: "Known applicable", render: (r: any) => r.coverage ? r.coverage.known_applicable_resources : "N/A" },
            { key: "vp", header: "Verified protected", render: (r: any) => r.coverage ? ratioText(r.coverage.verified_protected_ratio) : "N/A" },
            { key: "ee", header: "Effective exemptions", render: (r: any) => r.coverage?.effective_exemptions ?? "N/A" },
            { key: "ue", header: "Unknown enforcement", render: (r: any) => r.coverage?.unknown_enforcement ?? "N/A" },
            { key: "ua", header: "Unknown applicability", render: (r: any) => r.coverage?.unknown_applicability ?? "N/A" },
            { key: "nc", header: "Remaining non-compliant", render: (r: any) => r.links.assessment && r.coverage
                ? <Link className="text-sky-700 underline" href={`${r.links.assessment}?configuration_result=NON_COMPLIANT`}>{r.coverage.remaining_noncompliant}</Link> : "N/A" },
          ]}
        />
      </Section>

      <div className="grid gap-6 lg:grid-cols-2">
        <Section title="Gaps vs existing native coverage" description={data.gaps_vs_existing_native_coverage.definition}>
          {Object.keys(data.gaps_vs_existing_native_coverage.baseline_counts).length === 0
            ? <Empty message="No completed assessments yet." />
            : <DataTable rows={Object.entries(data.gaps_vs_existing_native_coverage.baseline_counts) as [string, number][]}
                         rowKey={([k]) => k}
                         columns={[{ key: "k", header: "Existing coverage", render: ([k]) => humanize(k) },
                                   { key: "v", header: "Pairs", render: ([, v]) => v }]} />}
        </Section>
        <Section title="Exceptions" actions={<Link className="text-sm text-sky-700 underline" href="/exceptions">All exceptions</Link>}>
          <ul className="space-y-1 text-sm">
            {Object.entries(exc.by_effective_status).map(([k, v]) => (
              <li key={k}><Link className="underline" href={`/exceptions?effective_status=${k}`}><StatusBadge value={k} /></Link> {String(v)}</li>
            ))}
            <li>Expiring within {exc.expiring_within_days.days} days: <Link className="text-sky-700 underline" href="/exceptions?expiring=true">{exc.expiring_within_days.ids.length}</Link></li>
            <li>Pending native cleanup/removal: <Link className="text-sky-700 underline" href="/exceptions?native_cleanup_pending=true">{exc.pending_native_cleanup.ids.length}</Link> (open work items: {exc.pending_native_cleanup.open_work})</li>
          </ul>
        </Section>
        <Section title="Readiness and approval bottlenecks" actions={<Link className="text-sm text-sky-700 underline" href="/rollouts">Rollouts</Link>}>
          <ul className="space-y-1 text-sm">
            <li>Packages awaiting security approval: {data.bottlenecks.packages_awaiting_security_approval.length}</li>
            <li>Packages awaiting Cloud Engineering approval: {data.bottlenecks.packages_awaiting_engineering_approval.length}</li>
            <li>Packages with failing pre-approval gates: {data.bottlenecks.packages_with_failing_pre_approval_gates.length}</li>
          </ul>
          <h3 className="mt-3 text-sm font-medium">Open readiness blockers by kind</h3>
          {Object.keys(data.bottlenecks.readiness_blockers_by_kind).length === 0 ? <Empty message="No blockers recorded." /> : (
            <ul className="text-sm">
              {Object.entries(data.bottlenecks.readiness_blockers_by_kind).map(([k, v]) => <li key={k}>{humanize(k)}: {String(v)}</li>)}
            </ul>
          )}
        </Section>
        <Section title="Delivery and drift" description={data.delivery.provenance}>
          {Object.keys(data.delivery.targets_by_state).length === 0 ? <Empty message="Nothing exported yet." /> : (
            <ul className="space-y-1 text-sm">
              {Object.entries(data.delivery.targets_by_state).map(([k, v]) => <li key={k}><StatusBadge value={k} /> {String(v)}</li>)}
            </ul>
          )}
          <p className="mt-2 text-sm">Drifted targets: {data.delivery.drifted_targets}; open drift work: {data.delivery.open_drift_work}</p>
          <h3 className="mt-3 text-sm font-medium">Open follow-up work</h3>
          <ul className="text-sm">
            {Object.entries(data.open_work_by_kind).map(([k, v]) => <li key={k}>{humanize(k)}: {String(v)}</li>)}
          </ul>
        </Section>
      </div>

      <Section title="Outcome metrics" description="Shown so their absence is explicit. Posture findings are never used to infer prevented attacks or runtime events.">
        <DataTable rows={data.outcome_metrics} rowKey={(r: any) => r.id}
                   columns={[{ key: "id", header: "Metric", render: (r: any) => humanize(r.id) },
                             { key: "s", header: "Status", render: (r: any) => <StatusBadge value={r.status === "UNAVAILABLE" ? "NOT_APPLICABLE" : r.status} label={r.status === "UNAVAILABLE" ? "Unavailable" : undefined} /> },
                             { key: "r", header: "Why", render: (r: any) => r.reason }]} />
      </Section>
    </div>
  );
}
