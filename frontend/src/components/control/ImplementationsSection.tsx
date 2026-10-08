"use client";

import Link from "next/link";
import { useState } from "react";
import { api, hasRole } from "@/lib/api";
import { useCommand } from "@/lib/hooks";
import { useSession } from "@/lib/session";
import { fmtTime, humanize, leaf, shortDigest } from "@/lib/format";
import { Button, DataTable, ErrorBox, KeyValue, Notice, Section, Select, StatusBadge, TextInput } from "@/components/ui";

const AZURE_SEARCH_PNA = "/providers/Microsoft.Authorization/policyDefinitions/ee980b6d-0eca-4501-8d54-f6290fd512c3";

function ImplementationCard({ info, reload }: { info: any; reload: () => void }) {
  const { user } = useSession();
  const cmd = useCommand();
  const rev = info.revisions[info.revisions.length - 1];
  const v = info.latest_validation;
  const canAuthor = hasRole(user, "CONTROL_ENGINEER");
  const validate = () => cmd.run(async () => {
    await api(`/implementation-revisions/${rev.id}/validate`, { method: "POST" });
    reload();
  });
  const submit = () => cmd.run(async () => {
    await api(`/implementation-revisions/${rev.id}/submit`, { method: "POST", body: { expected_lock_version: rev.lock_version } });
    reload();
  });
  return (
    <div className="mb-4 rounded border border-slate-200 p-4 dark:border-slate-700" data-testid={`impl-${info.implementation.id}`}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <Link href={`/implementations/${info.implementation.id}`} className="font-medium text-sky-700 underline">{info.implementation.name}</Link>
          <span className="ml-2 text-xs text-slate-500">{info.implementation.provider.toUpperCase()} · {humanize(info.implementation.mechanism_role)} · {info.implementation.management === "MANAGED_HERE" ? "Authored here; delivered by Cloud Engineering" : humanize(info.implementation.management)}</span>
        </div>
        <div className="flex gap-2">
          <StatusBadge value={rev.status} label={`r${rev.revision} ${humanize(rev.status)}`} />
          <StatusBadge value={rev.verification.status} />
        </div>
      </div>
      <KeyValue items={[
        ["Policy kind", humanize(rev.policy_kind)],
        ["Source", <code key="s" className="break-all text-xs">{rev.source_ref}</code>],
        ["Pinned version / digest", <span key="p">{rev.pinned_version ?? "-"} · <code className="text-xs">{shortDigest(rev.content_digest)}</code></span>],
        ["Parameters", <code key="pa" className="text-xs">{JSON.stringify(rev.parameters)}</code>],
        ["Assignment settings", <code key="as" className="text-xs">{JSON.stringify(rev.assignment_settings)}</code>],
        ["Audit/observation mode", info.capabilities.native_audit_mode],
        ["Native expiry for exceptions", info.capabilities.native_expiry_supported ? "Supported" : "Not supported (removal handoff required)"],
      ]} />
      <div className="mt-3">
        <h4 className="text-sm font-semibold">Feasibility and validation</h4>
        {!v ? <p className="text-sm text-slate-500">Not validated for the current content.</p> : (
          <div className="text-sm">
            <p className="my-1"><StatusBadge value={v.outcome} /> evaluator {v.evaluator_id ?? "none"} {v.evaluator_version ?? ""} · validated {fmtTime(v.validated_at)} · integration ready: <StatusBadge value={v.integration_ready ? "PASS" : "FAIL"} label={v.integration_ready ? "Yes" : "No"} /></p>
            {v.integration_blockers.length > 0 && <Notice tone="warning">{v.integration_blockers.join(" ")}</Notice>}
            <ul className="mt-2 space-y-0.5">
              {v.checks.map((c: any) => <li key={c.id}><StatusBadge value={c.status} /> <span className="text-xs text-slate-500">{c.id}</span> {c.message}</li>)}
            </ul>
            {v.test_results.length > 0 && <p className="mt-1 text-xs text-slate-600">Evaluator tests: {v.test_results.filter((t: any) => t.passed).length}/{v.test_results.length} passed</p>}
            {v.possible_duplicates.length > 0 && (
              <div className="mt-2">
                <h5 className="text-xs font-semibold uppercase text-slate-500">Possible duplicates / overlaps (not a semantic-equivalence claim)</h5>
                <ul className="text-xs">
                  {v.possible_duplicates.map((d: any) => <li key={d.binding_id}>{leaf(d.native_id)} at {d.scope_id} ({humanize(d.management)}): {d.reasons.join(", ")}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
      <ErrorBox error={cmd.error} />
      {canAuthor && (
        <div className="mt-3 flex gap-2">
          <Button variant="secondary" onClick={validate} disabled={cmd.pending} testId={`validate-${info.implementation.id}`}>Validate</Button>
          {rev.status === "DRAFT" && <Button onClick={submit} disabled={cmd.pending || v?.outcome !== "PASS"} testId={`submit-impl-${info.implementation.id}`}>Submit revision {rev.revision}</Button>}
        </div>
      )}
    </div>
  );
}

function NewAzureImplementation({ controlId, reload }: { controlId: string; reload: () => void }) {
  const cmd = useCommand();
  const [effect, setEffect] = useState("Deny");
  const [ref, setRef] = useState(AZURE_SEARCH_PNA);
  const [version, setVersion] = useState("1.0.1");
  const create = () => cmd.run(async () => {
    await api(`/controls/${controlId}/implementations`, { method: "POST", body: {
      provider: "azure", name: "Built-in assignment", mechanism_role: "PRIMARY_GUARDRAIL",
      policy_kind: "AZURE_POLICY_BUILTIN_ASSIGNMENT", source_kind: "BUILT_IN", source_ref: ref, pinned_version: version,
      parameters: { effect }, assignment_settings: { enforcementMode: "Default" }, change_reason: "Reference built-in definition",
    } });
    reload();
  });
  return (
    <details className="mt-2">
      <summary className="cursor-pointer text-sm text-sky-700">Add an Azure built-in policy reference</summary>
      <div className="mt-2 grid gap-2 md:grid-cols-3">
        <TextInput label="Built-in definition id" value={ref} onChange={setRef} />
        <TextInput label="Pinned version" value={version} onChange={setVersion} />
        <Select label="Effect" value={effect} onChange={setEffect} options={["Audit", "Deny", "Disabled"].map((x) => ({ value: x, label: x }))} />
      </div>
      <p className="mt-1 text-xs text-slate-500">The server pins the verified definition copy and decides verification status; unknown definitions remain unverified.</p>
      <ErrorBox error={cmd.error} />
      <div className="mt-2"><Button onClick={create} disabled={cmd.pending}>Create draft implementation</Button></div>
    </details>
  );
}

export function ImplementationsSection({ detail, reload }: { detail: any; reload: () => void }) {
  const { user } = useSession();
  return (
    <Section title="Provider implementations" id="implementations"
      description="Each implementation is validated against an explicit provider capability contract and a versioned evaluator.">
      {detail.implementations.length === 0 && <p className="text-sm text-slate-500">No implementations yet.</p>}
      {detail.implementations.map((i: any) => <ImplementationCard key={i.implementation.id} info={i} reload={reload} />)}
      {hasRole(user, "CONTROL_ENGINEER") && detail.current_revision.providers.includes("azure") &&
        <NewAzureImplementation controlId={detail.control.id} reload={reload} />}
    </Section>
  );
}

export function BaselineSection({ detail }: { detail: any }) {
  return (
    <Section title="Existing native baseline" id="baseline"
      description="Actual assignments/attachments (bindings) at scopes, separate from definitions. Coverage is only 'observed' with a fresh observation.">
      <DataTable rows={detail.existing_bindings} rowKey={(b: any) => b.id} empty="No bindings recorded for this control's providers." columns={[
        { key: "n", header: "Binding", render: (b: any) => <span><code className="text-xs">{leaf(b.native_id)}</code><div className="text-xs text-slate-500">{humanize(b.origin)}</div></span> },
        { key: "scope", header: "Scope", render: (b: any) => b.target_scope_id },
        { key: "def", header: "Definition", render: (b: any) => <code className="text-xs">{leaf(b.definition_ref)}</code> },
        { key: "set", header: "Settings", render: (b: any) => <code className="text-xs">{JSON.stringify(b.settings)}</code> },
        { key: "m", header: "Managed by", render: (b: any) => humanize(b.management) },
        { key: "obs", header: "Observed", render: (b: any) => b.observed_at ? <span>{fmtTime(b.observed_at)} <span className="text-xs text-slate-500">({humanize(b.evidence_provenance)})</span></span> : "Never" },
        { key: "drift", header: "Desired = observed", render: (b: any) => b.observed_state ? <StatusBadge value={JSON.stringify(b.observed_state) === JSON.stringify(b.desired_state) ? "MATCH" : "MISMATCH"} /> : <StatusBadge value="UNKNOWN" /> },
      ]} />
    </Section>
  );
}
