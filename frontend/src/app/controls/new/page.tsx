"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useCommand } from "@/lib/hooks";
import { Button, ErrorBox, Notice, PageHeader, Section, Select, TextInput } from "@/components/ui";

const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

export default function NewControlPage() {
  const router = useRouter();
  const cmd = useCommand();
  const [f, setF] = useState({
    id: "CTL-", origin: "PROPOSED", operational_owner: "Cloud Engineering", name: "", description: "",
    security_objective: "", rationale: "", evidence_kind: "WIZ_ISSUE", evidence_ref: "", evidence_summary: "",
    severity: "HIGH", provider: "azure", resource_types: "", applicability_criteria: "", security_owner: "",
    engineering_owner: "", framework_refs: "", exception_eligible: "true", prevention_boundary: "", limitations: "",
    change_reason: "",
  });
  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });

  const submit = () => cmd.run(async () => {
    const body = {
      id: f.id, origin: f.origin, operational_owner: f.operational_owner, name: f.name, description: f.description,
      security_objective: f.security_objective, rationale: f.rationale,
      source_evidence: f.evidence_ref ? [{ kind: f.evidence_kind, reference: f.evidence_ref, summary: f.evidence_summary || f.evidence_ref }] : [],
      severity: f.severity, providers: [f.provider], resource_types: lines(f.resource_types),
      applicability_criteria: f.applicability_criteria, security_owner: f.security_owner,
      engineering_owner: f.engineering_owner, framework_refs: lines(f.framework_refs),
      exception_eligible: f.exception_eligible === "true", prevention_boundary: f.prevention_boundary,
      limitations: lines(f.limitations), change_reason: f.change_reason,
    };
    const created = await api<any>("/controls", { method: "POST", body });
    router.push(`/controls/${created.control.id}`);
  });

  return (
    <div>
      <PageHeader title="Propose or register a control"
                  subtitle="State the security intent without provider syntax. Implementations are added on the control page." />
      <Notice>
        Registering an existing control as <em>Imported</em> or <em>Externally managed</em> catalogues it for coverage and duplicate
        detection. It does not transfer operational ownership.
      </Notice>
      <Section title="Intent">
        <div className="grid gap-3 md:grid-cols-2">
          <TextInput label="Control id (CTL-...)" value={f.id} onChange={set("id")} testId="f-id" />
          <Select label="Origin" value={f.origin} onChange={set("origin")} options={[
            { value: "PROPOSED", label: "Proposed (new)" }, { value: "IMPORTED", label: "Imported" },
            { value: "EXTERNALLY_MANAGED", label: "Externally managed" }]} />
          <TextInput label="Name" value={f.name} onChange={set("name")} testId="f-name" />
          <TextInput label="Operational owner" value={f.operational_owner} onChange={set("operational_owner")} />
          <TextInput label="Security objective (what must become impossible, where)" value={f.security_objective} onChange={set("security_objective")} multiline />
          <TextInput label="Description" value={f.description} onChange={set("description")} multiline />
          <TextInput label="Rationale" value={f.rationale} onChange={set("rationale")} multiline />
          <TextInput label="Prevention boundary (what is and is not prevented)" value={f.prevention_boundary} onChange={set("prevention_boundary")} multiline />
          <TextInput label="Limitations (one per line)" value={f.limitations} onChange={set("limitations")} multiline />
          <TextInput label="Applicability criteria" value={f.applicability_criteria} onChange={set("applicability_criteria")} multiline />
        </div>
      </Section>
      <Section title="Evidence, scope and owners">
        <div className="grid gap-3 md:grid-cols-3">
          <Select label="Evidence kind" value={f.evidence_kind} onChange={set("evidence_kind")} options={
            ["WIZ_ISSUE", "INCIDENT", "FINDING", "AUDIT", "THREAT_INTEL", "OTHER"].map((v) => ({ value: v, label: v }))} />
          <TextInput label="Evidence reference" value={f.evidence_ref} onChange={set("evidence_ref")} />
          <TextInput label="Evidence summary" value={f.evidence_summary} onChange={set("evidence_summary")} />
          <Select label="Severity" value={f.severity} onChange={set("severity")} options={
            ["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((v) => ({ value: v, label: v }))} />
          <Select label="Provider" value={f.provider} onChange={set("provider")} options={[
            { value: "azure", label: "Azure" }, { value: "aws", label: "AWS" }]} />
          <TextInput label="Resource types (one per line)" value={f.resource_types} onChange={set("resource_types")} multiline />
          <TextInput label="Security owner" value={f.security_owner} onChange={set("security_owner")} />
          <TextInput label="Engineering owner" value={f.engineering_owner} onChange={set("engineering_owner")} />
          <TextInput label="Framework references (one per line)" value={f.framework_refs} onChange={set("framework_refs")} multiline />
          <Select label="Exception eligible" value={f.exception_eligible} onChange={set("exception_eligible")} options={[
            { value: "true", label: "Yes" }, { value: "false", label: "No" }]} />
          <TextInput label="Change reason" value={f.change_reason} onChange={set("change_reason")} />
        </div>
      </Section>
      <ErrorBox error={cmd.error} />
      <Button onClick={submit} disabled={cmd.pending} testId="f-submit">{cmd.pending ? "Saving…" : "Create draft revision 1"}</Button>
    </div>
  );
}
