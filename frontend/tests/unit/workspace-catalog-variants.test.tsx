import { fireEvent, render, screen, within } from "@testing-library/react";
import { A2uiCanvas } from "@/components/workspace/A2uiCanvas";
import { CATALOG_ID } from "@/components/workspace/catalog/contracts";
import fixture from "../fixtures/investigation-sample.json";

// Variants of the domain components that the seeded demo investigations do not reach (plans, packages, bundles,
// unavailable data, outdated basis). Shapes follow backend/app/workspace/tools.py.

vi.mock("next/link", () => ({ default: ({ href, children }: any) => <a href={href}>{children}</a> }));

const views: Record<string, any> = (fixture as any).a2ui.messages.find((m: any) => m.updateDataModel).updateDataModel.value.views;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

function single(component: string, view: unknown, title = "Panel under test"): any[] {
  return [
    { version: "v0.9", createSurface: { surfaceId: "ws-variant", catalogId: CATALOG_ID } },
    { version: "v0.9", updateDataModel: { surfaceId: "ws-variant", path: "/", value: { views: { v: view } } } },
    { version: "v0.9", updateComponents: { surfaceId: "ws-variant", components: [
      { id: "root", component: "CanvasStack", children: ["c_v"] },
      { id: "c_v", component, title, data: { path: "/views/v" } },
    ] } },
  ];
}

async function show(component: string, view: unknown, onAction = vi.fn()) {
  render(<A2uiCanvas messages={single(component, view)} onAction={onAction} />);
  const panel = await screen.findByTestId(`ws-component-${component}`);
  expect(within(panel).queryByRole("alert")).toBeNull();
  return { panel, onAction };
}

describe("governance views with a plan, package and bundle", () => {
  it("marks the current rollout stage with text, not colour alone", async () => {
    const { panel } = await show("RolloutTimeline", {
      plan: { id: "plan-1", title: "Search PNA rollout", stage: "PILOT", state: "ACTIVE", target_scope_id: "az-mg-prod",
              href: "/controls/CTL-AZ-SEARCH-PNA" },
      suggested: false,
      stages: [
        { stage: "OBSERVATION", status: "DONE", scope_ids: ["az-sub-sandbox"], settings_summary: "effect=Audit" },
        { stage: "PILOT", status: "CURRENT", scope_ids: ["az-rg-retail-search"], settings_summary: "effect=Deny" },
        { stage: "LIMITED", status: "PLANNED", scope_ids: ["az-sub-retail-prod"], settings_summary: "effect=Deny" },
        { stage: "BROAD", status: "NOT_PLANNED", scope_ids: [], settings_summary: "-" },
      ],
      note: "Plan stage PILOT (ACTIVE).",
    });
    const current = panel.querySelector("[aria-current='step']");
    expect(current).toHaveTextContent("Current");
    expect(current).toHaveTextContent("Pilot");
    expect(within(panel).getByRole("link", { name: "Search PNA rollout" })).toHaveAttribute("href", "/controls/CTL-AZ-SEARCH-PNA");
    expect(within(panel).getByText("Not planned")).toBeInTheDocument();
  });

  it("labels an unsaved suggested rollout", async () => {
    const { panel } = await show("RolloutTimeline", { ...views.rollout, suggested: true });
    expect(within(panel).getByText("Suggested template, not saved")).toBeInTheDocument();
  });

  it("shows digest-bound decisions and failing gates without offering to approve", async () => {
    const { panel } = await show("ApprovalStatus", {
      package: { id: "pkg-1", revision: 2, status: "IN_REVIEW", manifest_digest: "sha256:0123456789abcdef0123", through_stage: "PILOT",
                 href: "/controls/CTL-AZ-SEARCH-PNA" },
      required_roles: ["SECURITY_APPROVER", "CLOUD_ENGINEER"],
      decisions: [
        { role: "SECURITY_APPROVER", actor_id: "u-sam", decision: "APPROVED", decided_at: "2026-10-08T10:00:00Z", bound_to_current_digest: true },
        { role: "CLOUD_ENGINEER", actor_id: "u-eli", decision: "APPROVED", decided_at: "2026-10-07T10:00:00Z", bound_to_current_digest: false },
      ],
      failing_gates: [{ id: "exceptions_applied", label: "Approved exceptions applied", phase: "SUBMIT", detail: "{\"missing\": 1}" }],
      note: "Approve or reject in the Classic Experience.",
    });
    expect(within(panel).getByTestId("ws-approval-classic")).toHaveTextContent("Approvals happen in the Classic Experience");
    expect(within(panel).getByText("Bound")).toBeInTheDocument();
    expect(within(panel).getByText("Not bound (does not count)")).toBeInTheDocument();
    expect(within(panel).getByText("Approved exceptions applied")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "pkg-1" })).toHaveAttribute("href", "/controls/CTL-AZ-SEARCH-PNA");
    expect(within(panel).queryByRole("button")).toBeNull();
  });

  it("lists bundle files (objects with a path key stay data, not bindings)", async () => {
    const { panel } = await show("GitOpsHandoffPreview", {
      bundle: { id: "bnd-1", kind: "AZURE_POLICY", banner: "APPROVED - NOT DEPLOYED", bundle_digest: "sha256:abcdef0123456789",
                exported_at: "2026-10-08T11:00:00Z", href: "/bundles/bnd-1",
                files: [{ path: "manifest.json", sha256: "sha256:111111111111111111" }, { path: "assignments/pilot.json", sha256: "sha256:22222222222222" }] },
      package_status: "APPROVED",
      note: "Approved or exported does not mean deployed.",
    });
    expect(within(panel).getByTestId("ws-not-deployed")).toHaveTextContent("not deployed");
    expect(within(panel).getByText("manifest.json")).toBeInTheDocument();
    expect(within(panel).getByText("assignments/pilot.json")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "bnd-1" })).toHaveAttribute("href", "/bundles/bnd-1");
  });

  it("flags exceptions that expire soon", async () => {
    const exceptions = clone(views.exceptions);
    exceptions.items[0].expiring_soon = true;
    exceptions.items[0].days_until_expiry = 5;
    const { panel } = await show("ExceptionReview", exceptions);
    expect(within(panel).getByText("Expiring soon")).toBeInTheDocument();
    expect(within(panel).getByText("in 5 days")).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "exc-az-payments-legacy" })).toHaveAttribute("href", "/exceptions/exc-az-payments-legacy");
  });
});

describe("unavailable and outdated data", () => {
  it("says coverage is unknown when no assessment covers the scope", async () => {
    const { panel } = await show("ControlCoverageMatrix", {
      ...views.coverage, available: false, assessment_run_id: null, rows: [], totals: {}, verified_protected_ratio: "N/A",
      unavailable_reason: "No completed assessment covers this scope, so coverage is unknown.",
    });
    expect(within(panel).getByText("Coverage is unknown")).toBeInTheDocument();
    expect(within(panel).queryByTestId("ws-coverage-table")).toBeNull();
  });

  it("says readiness is unknown without an assessment", async () => {
    const { panel } = await show("ApplicationReadinessPanel", {
      available: false, run_id: null, applications: [], evidence: [],
      note: "No assessment covers this scope, so readiness per resource is UNKNOWN.",
    });
    expect(within(panel).getByText("Readiness per resource is UNKNOWN")).toBeInTheDocument();
  });

  it("warns about an outdated basis and offers a confirmed re-run", async () => {
    const impact = { ...clone(views.impact), basis_current: false,
                     basis_notes: ["Assessment used implementation revision r1; the latest is r2."] };
    const { panel, onAction } = await show("ImpactAssessment", impact);
    expect(within(panel).getByText("Basis outdated")).toBeInTheDocument();
    expect(within(panel).getByTestId("ws-basis-notes")).toHaveTextContent("the latest is r2");
    fireEvent.click(within(panel).getByRole("button", { name: "Run a fresh impact assessment…" }));
    expect(onAction).toHaveBeenCalledWith(expect.objectContaining({ name: "ws.run_assessment" }));
  });

  it("does not claim demo data for live sources", async () => {
    const { panel } = await show("EvidencePanel", { ...views.evidence, demo_data: false });
    expect(within(panel).queryByTestId("ws-demo-data")).toBeNull();
    expect(within(panel).getByText("Live sources")).toBeInTheDocument();
  });
});
