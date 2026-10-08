"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, hasGlobalRole, hasRole } from "@/lib/api";
import { useApi, useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize, leaf, shortDigest } from "@/lib/format";
import { Button, CodeBlock, DataTable, ErrorBox, Notice, Section, Select, StatusBadge } from "@/components/ui";

const STAGES = ["ASSESSMENT", "OBSERVATION", "PILOT", "LIMITED", "BROAD"];

export function GateList({ gates }: { gates: any[] }) {
  return (
    <ul className="space-y-0.5 text-sm" data-testid="gate-list">
      {gates.map((g) => (
        <li key={g.id} data-gate={g.id} data-gate-status={g.status}>
          <StatusBadge value={g.status} /> <span className="font-medium">{g.label}</span>
          <span className="ml-1 text-xs text-slate-500">
            {typeof g.detail === "string" ? g.detail : Array.isArray(g.detail) ? g.detail.map((d: any) => typeof d === "string" ? d : d.message ?? JSON.stringify(d)).join("; ") : JSON.stringify(g.detail)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function CreatePlan({ detail, reload }: { detail: any; reload: () => void }) {
  const cmd = useCommand();
  const impls = detail.implementations;
  const [implRev, setImplRev] = useState(impls[0]?.revisions[impls[0].revisions.length - 1]?.id ?? "");
  const tpl = useApi<any>(implRev ? `/rollout-plans/template?control_id=${detail.control.id}&implementation_revision_id=${implRev}` : null);
  const provider = detail.current_revision.providers[0];
  const scopes = useApi<any>(`/scopes?provider=${provider}`);
  const [pilot, setPilot] = useState("");
  const [rings, setRings] = useState<any[]>([]);
  useEffect(() => {
    if (tpl.data) {
      setRings(tpl.data.rings);
      const p = tpl.data.rings.find((r: any) => r.stage === "PILOT");
      setPilot(p?.scope_ids[0] ?? "");
    }
  }, [tpl.data]);
  const candidates = (scopes.data?.items ?? []).filter((s: any) => ["AZURE_SUBSCRIPTION", "AWS_ACCOUNT"].includes(s.scope_type) && !s.is_management_account);
  const create = () => cmd.run(async () => {
    const t = tpl.data;
    const finalRings = rings.map((r) => r.stage === "PILOT" ? { ...r, scope_ids: [pilot] } : r);
    await api("/rollout-plans", { method: "POST", body: {
      control_id: detail.control.id, title: t.title, implementation_revision_id: implRev, target_scope_id: t.target_scope_id,
      rings: finalRings, pause_criteria: t.pause_criteria, rollback_plan: t.rollback_plan,
      prerequisite_changes: t.prerequisite_changes, deployment_instructions: t.deployment_instructions,
    } });
    reload();
  });
  if (!impls.length) return <p className="text-sm text-slate-500">Add an implementation first.</p>;
  return (
    <div className="rounded border border-dashed border-slate-300 p-4 dark:border-slate-600" data-testid="create-plan">
      <h3 className="mb-2 text-sm font-semibold">Create a rollout plan from the provider template</h3>
      <div className="flex flex-wrap items-end gap-2">
        <Select label="Implementation revision" value={implRev} onChange={setImplRev}
                options={impls.map((i: any) => { const r = i.revisions[i.revisions.length - 1]; return { value: r.id, label: `${i.implementation.name} r${r.revision}` }; })} />
        <Select label="Pilot scope" value={pilot} onChange={setPilot} testId="pilot-scope"
                options={candidates.map((s: any) => ({ value: s.id, label: `${s.id} (${s.display_name})` }))} />
      </div>
      <ErrorBox error={tpl.error ?? cmd.error} />
      {tpl.data && (
        <div className="mt-3">
          <DataTable rows={rings.map((r) => r.stage === "PILOT" ? { ...r, scope_ids: [pilot] } : r)} rowKey={(r: any) => r.stage} columns={[
            { key: "s", header: "Ring", render: (r: any) => humanize(r.stage) },
            { key: "sc", header: "Scopes", render: (r: any) => r.scope_ids.join(", ") },
            { key: "set", header: "Native settings", render: (r: any) => <code className="text-xs">{JSON.stringify(r.settings)}</code> },
          ]} />
          <p className="mt-2 text-xs text-slate-500">Capability notes: {Object.entries(tpl.data.capability_notes).map(([k, v]) => `${k}: ${v}`).join(" ")}</p>
          <div className="mt-2"><Button onClick={create} disabled={cmd.pending || !pilot} testId="create-plan-btn">Create plan</Button></div>
        </div>
      )}
    </div>
  );
}

function DeliveryTargets({ pkg, plan, reload }: { pkg: any; plan: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const [scenario, setScenario] = useState("SUCCESS");
  const [obsScenario, setObsScenario] = useState("MATCH");
  const approvedBundle = pkg.bundles.find((b: any) => b.kind === "APPROVED_HANDOFF");
  const rings = Array.from(new Set(pkg.delivery_targets.map((t: any) => t.ring_stage))) as string[];
  const [ring, setRing] = useState(rings[0] ?? "PILOT");
  const isCE = hasGlobalRole(user, "CLOUD_ENGINEER");
  const runPipeline = () => cmd.run(async () => {
    await api("/mock-pipeline/runs", { method: "POST", body: { bundle_id: approvedBundle.id, ring, scenario } });
    reload();
  });
  const observe = (id: string) => cmd.run(async () => {
    await api("/mock-observations", { method: "POST", body: { delivery_target_id: id, scenario: obsScenario } });
    reload();
  });
  if (!pkg.delivery_targets.length) return null;
  return (
    <div className="mt-3" data-testid="delivery">
      <h4 className="text-sm font-semibold">Delivery state (MOCK provenance)</h4>
      <p className="text-xs text-slate-500">Exported or approved is not deployed. A pipeline success is not verification: only a fresh, matching observation after a valid APPLIED receipt marks a target VERIFIED.</p>
      <DataTable rows={pkg.delivery_targets} rowKey={(t: any) => t.id} columns={[
        { key: "r", header: "Ring", render: (t: any) => humanize(t.ring_stage) },
        { key: "k", header: "Artifact", render: (t: any) => humanize(t.artifact_kind) },
        { key: "n", header: "Native identity", render: (t: any) => <code className="break-all text-xs">{leaf(t.native_identity)}</code> },
        { key: "s", header: "Scope", render: (t: any) => t.scope_id },
        { key: "st", header: "State", render: (t: any) => <span data-testid={`target-state-${t.artifact_kind}`}><StatusBadge value={t.state} /></span> },
        { key: "v", header: "Verified at", render: (t: any) => fmtTime(t.verified_at) },
        { key: "a", header: "", render: (t: any) => isCE ? <Button variant="secondary" onClick={() => observe(t.id)} disabled={cmd.pending} testId={`observe-${t.artifact_kind}`}>Record fixture observation</Button> : null },
      ]} />
      {isCE && approvedBundle && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          <Select label="Ring" value={ring} onChange={setRing} options={rings.map((r) => ({ value: r, label: humanize(r) }))} />
          <Select label="Mock pipeline scenario" value={scenario} onChange={setScenario} testId="pipeline-scenario"
                  options={["SUCCESS", "FAILURE", "STALE", "MISMATCH"].map((x) => ({ value: x, label: humanize(x) }))} />
          <Button onClick={runPipeline} disabled={cmd.pending} testId="run-pipeline">Run mock pipeline</Button>
          <Select label="Observation scenario" value={obsScenario} onChange={setObsScenario} testId="observation-scenario"
                  options={["MATCH", "DRIFT", "MISSING"].map((x) => ({ value: x, label: humanize(x) }))} />
        </div>
      )}
      <ErrorBox error={cmd.error} />
      {cmd.result?.receipts && (
        <ul className="mt-2 text-xs" data-testid="pipeline-result">
          {cmd.result.receipts.map((r: any) => <li key={r.id}><StatusBadge value={r.validation_status} /> {r.receipt_key} {r.validation_notes.join(" ")}</li>)}
        </ul>
      )}
      <p className="mt-1 text-xs text-slate-500">Plan stage {humanize(plan.stage)}: receipts for later rings are rejected until Cloud Engineering advances the rollout.</p>
    </div>
  );
}

function PackageCard({ pkg, plan, reload }: { pkg: any; plan: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const gates = useApi<any>(pkg.status === "SUPERSEDED" ? null : `/change-packages/${pkg.id}/gates`);
  const roles = ["SECURITY_APPROVER", "CLOUD_ENGINEER"].filter((r) => hasGlobalRole(user, r));
  const [role, setRole] = useState(roles[0] ?? "");
  const [manifest, setManifest] = useState<any>(null);
  const decide = (decision: string) => cmd.run(async () => {
    const rationale = window.prompt(`${decision === "APPROVE" ? "Approval" : "Rejection"} rationale (reviewing digest ${pkg.manifest_digest})`);
    if (!rationale) return;
    await api(`/change-packages/${pkg.id}/decisions`, { method: "POST", body: { decision, role: role || roles[0], expected_digest: pkg.manifest_digest, rationale } });
    reload();
    gates.reload();
  });
  const exportBundle = (draft: boolean) => cmd.run(async () => {
    await api(`/change-packages/${pkg.id}/${draft ? "draft-export" : "export"}`, { method: "POST" });
    reload();
  });
  const showManifest = () => cmd.run(async () => setManifest((await api<any>(`/change-packages/${pkg.id}`)).manifest));
  const canExport = hasRole(user, "CLOUD_ENGINEER") || hasRole(user, "CONTROL_ENGINEER");
  return (
    <div className="mb-3 rounded border border-slate-200 p-3 dark:border-slate-700" data-testid={`package-${pkg.package_revision}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm">
          <span className="font-medium">Package r{pkg.package_revision}</span> · through {humanize(pkg.through_stage)} ·{" "}
          <code className="text-xs" title={pkg.manifest_digest}>{shortDigest(pkg.manifest_digest)}</code> · by {pkg.created_by} · {fmtTime(pkg.created_at)}
        </div>
        <span data-testid="package-status"><StatusBadge value={pkg.status} /></span>
      </div>
      {pkg.status_reasons.length > 0 && <ul className="mt-1 text-xs text-slate-600">{pkg.status_reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>}
      {gates.data && <div className="mt-2"><h4 className="text-xs font-semibold uppercase text-slate-500">Gates (re-evaluated now)</h4><GateList gates={gates.data.gates} /></div>}
      {pkg.decisions.length > 0 && (
        <div className="mt-2">
          <h4 className="text-xs font-semibold uppercase text-slate-500">Decisions (bound to digest)</h4>
          <ul className="text-sm">{pkg.decisions.map((d: any) => (
            <li key={d.id}><StatusBadge value={d.decision === "APPROVE" ? "APPROVED" : "REJECTED"} label={d.decision} /> {humanize(d.role)} by {d.actor_id} at {fmtTime(d.decided_at)}: {d.rationale} <code className="text-xs">{shortDigest(d.subject_digest)}</code></li>
          ))}</ul>
        </div>
      )}
      <ErrorBox error={cmd.error} />
      <div className="mt-2 flex flex-wrap items-end gap-2">
        {pkg.status === "IN_REVIEW" && roles.length > 0 && (
          <>
            {roles.length > 1 && <Select label="Approve as" value={role} onChange={setRole} options={roles.map((r) => ({ value: r, label: humanize(r) }))} />}
            <Button onClick={() => decide("APPROVE")} disabled={cmd.pending} testId="approve-package">Approve as {humanize(role || roles[0])}</Button>
            <Button variant="danger" onClick={() => decide("REJECT")} disabled={cmd.pending}>Reject</Button>
          </>
        )}
        {pkg.status === "APPROVED" && canExport && <Button onClick={() => exportBundle(false)} disabled={cmd.pending} testId="export-bundle">Export approved handoff bundle</Button>}
        {canExport && pkg.status !== "SUPERSEDED" && <Button variant="secondary" onClick={() => exportBundle(true)} disabled={cmd.pending}>Draft export (unapproved)</Button>}
        <Button variant="secondary" onClick={showManifest} disabled={cmd.pending}>View manifest</Button>
      </div>
      {manifest && <div className="mt-2"><CodeBlock value={manifest} /></div>}
      {pkg.bundles.length > 0 && (
        <ul className="mt-2 text-sm">
          {pkg.bundles.map((b: any) => (
            <li key={b.id}><StatusBadge value={b.kind === "APPROVED_HANDOFF" ? "APPROVED" : "DRAFT"} label={humanize(b.kind)} />{" "}
              <Link className="text-sky-700 underline" href={`/bundles/${b.id}`}>{b.id}</Link> <code className="text-xs">{shortDigest(b.bundle_digest)}</code></li>
          ))}
        </ul>
      )}
      <DeliveryTargets pkg={pkg} plan={plan} reload={reload} />
    </div>
  );
}

function PlanCard({ plan, reload }: { plan: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const [through, setThrough] = useState(plan.rings[Math.min(1, plan.rings.length - 1)]?.stage ?? "PILOT");
  const nextStage = plan.rings.map((r: any) => r.stage).find((s: string) => STAGES.indexOf(s) > STAGES.indexOf(plan.stage));
  const submitPackage = () => cmd.run(async () => {
    await api(`/rollout-plans/${plan.id}/packages`, { method: "POST", body: { through_stage: through } });
    reload();
  });
  const advance = () => cmd.run(async () => {
    const reason = window.prompt(`Reason for advancing to ${nextStage}`);
    if (!reason) return;
    await api(`/rollout-plans/${plan.id}/advance`, { method: "POST", body: { to_stage: nextStage, expected_lock_version: plan.lock_version, reason } });
    reload();
  });
  const setState = (action: string) => cmd.run(async () => {
    const reason = window.prompt(`Reason to ${action} the rollout`);
    if (!reason) return;
    await api(`/rollout-plans/${plan.id}/${action}`, { method: "POST", body: { reason, expected_lock_version: plan.lock_version } });
    reload();
  });
  const isCE = hasGlobalRole(user, "CLOUD_ENGINEER");
  const isSec = hasGlobalRole(user, "SECURITY_APPROVER");
  return (
    <div className="mb-4 rounded border border-slate-200 p-4 dark:border-slate-700" data-testid="plan-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="font-medium">{plan.title}</span>
          <span className="ml-2 text-xs text-slate-500">{plan.id} · target {plan.target_scope_id}</span>
        </div>
        <div className="flex gap-2 text-sm">
          <span data-testid="plan-stage"><StatusBadge value="ACTIVE" label={`Stage: ${humanize(plan.stage)}`} /></span>
          <StatusBadge value={plan.state} />
        </div>
      </div>
      <DataTable rows={plan.rings} rowKey={(r: any) => r.stage} columns={[
        { key: "s", header: "Ring", render: (r: any) => <span>{humanize(r.stage)}{r.stage === plan.stage && <span className="ml-1 text-xs text-sky-700">(current)</span>}</span> },
        { key: "sc", header: "Scopes", render: (r: any) => r.scope_ids.join(", ") },
        { key: "set", header: "Native settings", render: (r: any) => <code className="text-xs">{JSON.stringify(r.settings)}</code> },
      ]} />
      <p className="mt-2 text-xs text-slate-600">Rollback: {plan.rollback_plan.steps.join(" ")} Limitations: {plan.rollback_plan.limitations.join(" ")} Emergency path: {plan.rollback_plan.emergency_path}</p>
      <ErrorBox error={cmd.error} />
      <div className="mt-2 flex flex-wrap items-end gap-2">
        {hasRole(user, "CONTROL_ENGINEER") && plan.state !== "CANCELLED" && (
          <>
            <Select label="Package through ring" value={through} onChange={setThrough} options={plan.rings.map((r: any) => ({ value: r.stage, label: humanize(r.stage) }))} />
            <Button onClick={submitPackage} disabled={cmd.pending} testId="submit-package">Submit change package</Button>
          </>
        )}
        {isCE && plan.state === "ACTIVE" && nextStage && <Button onClick={advance} disabled={cmd.pending} testId="advance-rollout">Advance to {humanize(nextStage)}</Button>}
        {(isCE || isSec) && plan.state === "ACTIVE" && <Button variant="secondary" onClick={() => setState("pause")} disabled={cmd.pending}>Pause</Button>}
        {(isCE || isSec) && plan.state === "PAUSED" && <Button variant="secondary" onClick={() => setState("resume")} disabled={cmd.pending}>Resume</Button>}
        {(isCE || isSec) && plan.state !== "CANCELLED" && <Button variant="danger" onClick={() => setState("cancel")} disabled={cmd.pending}>Cancel rollout</Button>}
      </div>
      <h4 className="mb-2 mt-4 text-sm font-semibold">Change packages</h4>
      {plan.packages.length === 0 && <p className="text-sm text-slate-500">No package submitted.</p>}
      {plan.packages.map((p: any) => (
        <PackageCard key={`${p.id}-${p.lock_version}-${p.decisions.length}-${p.bundles.length}-${plan.lock_version}`}
                     pkg={p} plan={plan} reload={reload} />
      ))}
    </div>
  );
}

export function RolloutSection({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  const active = detail.rollout_plans.filter((p: any) => p.state !== "CANCELLED");
  return (
    <Section title="Rollout, approvals and handoff" id="rollout"
      description="Separate approvals by distinct identities bind to the exact package digest. Gates are enforced by the backend.">
      <Notice>Lifecycle, rollout stage, native settings and delivery state are separate. An approved or exported change is not deployed.</Notice>
      <div className="mt-3">
        {detail.rollout_plans.map((p: any) => <PlanCard key={p.id} plan={p} reload={reload} />)}
        {!active.length && hasRole(user, "CONTROL_ENGINEER") && (
          // Remount when a new assessment lands so the suggested pilot reflects current blockers.
          <CreatePlan key={detail.assessments[0]?.id ?? "no-assessment"} detail={detail} reload={reload} />
        )}
      </div>
    </Section>
  );
}
