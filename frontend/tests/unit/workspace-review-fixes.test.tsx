import { act, fireEvent, render, screen } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { FeaturesProvider } from "@/lib/features";
import type { Draft } from "@/lib/workspace";
import WorkspacePage from "@/app/workspace/page";
import { A2uiCanvas } from "@/components/workspace/A2uiCanvas";
import { DraftPanel } from "@/components/workspace/drafts/DraftPanel";
import { DATA_CONTRACTS, LinkSchema } from "@/components/workspace/catalog/contracts";
import sample from "../fixtures/investigation-sample.json";

// Regression tests for the frontend findings of the adversarial review.

const apiMock = vi.hoisted(() => vi.fn());
const nav = vi.hoisted(() => ({ router: { push: vi.fn(), replace: vi.fn() }, search: "" }));

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: apiMock }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => "/workspace",
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { user_id: "u-cara", username: "cara", display_name: "Cara", team: "Security", grants: [{ role: "CONTROL_ENGINEER", scope_ids: ["*"] }] },
    ready: true, logout: vi.fn(),
  }),
}));

const exceptionDraft: Draft = {
  kind: "exception",
  title: "Exception request for srch-analytics-unknown",
  basis: { control_revision_id: "crev-1" },
  payload: {
    control_id: "CTL-AZ-SEARCH-PNA", implementation_id: "impl-1", application: "", team: "Analytics",
    business_justification: "", technical_justification: "", scope_id: "az-rg-analytics", granularity: "RESOURCE",
    resource_ids: ["/subscriptions/x/srch-analytics-unknown"], principal_patterns: [], risk_owner: "",
    compensating_controls: [], valid_from: null, expires_at: "2026-11-07T12:00:00Z", work_ref: null,
  },
  required_fields: ["business_justification", "technical_justification", "risk_owner", "compensating_controls"],
  can_submit: true, blocking_reasons: [], preview: null, notes: [], submit_via: "POST /api/v1/workspace/drafts/submit",
};

const fill = (field: string, value: string) =>
  fireEvent.change(screen.getByTestId(`ws-draft-field-${field}`), { target: { value } });

function fillException() {
  fill("business_justification", "Analytics exploration needs public access this quarter.");
  fill("technical_justification", "Private endpoint rollout for analytics is scheduled.");
  fill("risk_owner", "Analytics lead");
  fill("compensating_controls", "IP allow-list");
}

beforeEach(() => {
  apiMock.mockReset();
  nav.router.push.mockReset();
  nav.router.replace.mockReset();
  nav.search = "";
});

describe("draft fixes", () => {
  it("asks for the application when the resource has none, and submits it", async () => {
    apiMock.mockResolvedValue({ created: { type: "exception", id: "exc-1", href: "/exceptions/exc-1" }, note: "ok" });
    const draft = { ...exceptionDraft, required_fields: ["application", ...exceptionDraft.required_fields] };
    render(<DraftPanel draft={draft} onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    fillException();
    expect(screen.getByTestId("ws-draft-submit")).toBeDisabled();
    fill("application", "analytics-explorer");
    fireEvent.click(screen.getByTestId("ws-draft-submit"));
    await act(async () => { fireEvent.click(screen.getByTestId("ws-confirm")); });
    expect(apiMock.mock.calls[0][1].body.payload.application).toBe("analytics-explorer");
  });

  it("keeps what the person wrote when a stale draft is prepared again", async () => {
    apiMock.mockRejectedValue(new ApiError(409, "DRAFT_STALE", "The draft is stale.", { changed: ["resource_snapshot_id"] }));
    const onPrepareAgain = vi.fn();
    const { unmount } = render(<DraftPanel draft={exceptionDraft} onPrepareAgain={onPrepareAgain} onDiscard={vi.fn()} />);
    fillException();
    fireEvent.click(screen.getByTestId("ws-draft-submit"));
    await act(async () => { fireEvent.click(screen.getByTestId("ws-confirm")); });
    fireEvent.click(await screen.findByTestId("ws-draft-prepare-again"));
    const kept = onPrepareAgain.mock.calls[0][0];
    expect(kept.business_justification).toContain("Analytics exploration");
    unmount();
    render(<DraftPanel draft={exceptionDraft} previousValues={kept} onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.getByTestId("ws-draft-field-business_justification")).toHaveValue(kept.business_justification);
    expect(screen.getByTestId("ws-draft-field-risk_owner")).toHaveValue("Analytics lead");
  });

  it("shows the reasons of a DRAFT_BLOCKED refusal and keeps the typed values", async () => {
    apiMock.mockRejectedValue(new ApiError(409, "DRAFT_BLOCKED", "The draft cannot be submitted.",
      { blocking_reasons: ["Existing control(s) already target this resource type: CTL-AZ-SEARCH-PNA."] }));
    render(<DraftPanel draft={exceptionDraft} onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    fillException();
    fireEvent.click(screen.getByTestId("ws-draft-submit"));
    await act(async () => { fireEvent.click(screen.getByTestId("ws-confirm")); });
    expect(await screen.findByTestId("ws-draft-blocked")).toHaveTextContent("CTL-AZ-SEARCH-PNA");
    expect(screen.queryByTestId("ws-draft-stale")).toBeNull();
    expect(screen.getByTestId("ws-draft-field-risk_owner")).toHaveValue("Analytics lead");
  });

  it("collects provider and resource type for a control proposal", async () => {
    apiMock.mockResolvedValue({ created: { type: "control", id: "CTL-AZ-KV", href: "/controls/CTL-AZ-KV" }, note: "ok" });
    const draft: Draft = {
      ...exceptionDraft, kind: "control", title: "New control proposal", basis: { matching_control_ids: [] },
      required_fields: ["id", "name", "providers", "resource_types", "security_objective", "rationale", "severity",
                        "applicability_criteria", "security_owner", "engineering_owner", "prevention_boundary"],
      payload: { id: "", name: "", description: "Key vault purge protection", security_objective: "", rationale: "",
        severity: "HIGH", providers: [], resource_types: [], applicability_criteria: "", security_owner: "",
        engineering_owner: "", prevention_boundary: "" },
    };
    render(<DraftPanel draft={draft} onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    for (const [k, v] of Object.entries({ id: "CTL-AZ-KV", name: "Key Vault purge protection", security_objective: "o",
      rationale: "r", applicability_criteria: "a", security_owner: "s", engineering_owner: "e", prevention_boundary: "b" })) {
      fill(k, v);
    }
    expect(screen.getByTestId("ws-draft-submit")).toBeDisabled();
    fill("provider", "azure");
    fill("resource_type", "Microsoft.KeyVault/vaults");
    fireEvent.click(screen.getByTestId("ws-draft-submit"));
    await act(async () => { fireEvent.click(screen.getByTestId("ws-confirm")); });
    const payload = apiMock.mock.calls[0][1].body.payload;
    expect(payload.providers).toEqual(["azure"]);
    expect(payload.resource_types).toEqual(["Microsoft.KeyVault/vaults"]);
  });
});

describe("contract length limits count code points like the server", () => {
  const lock = "\u{1F512}"; // one code point, two UTF-16 units
  it("accepts 120 emoji in a 120-character link label and rejects 121", () => {
    expect(LinkSchema.safeParse({ label: lock.repeat(120), href: "/dashboard" }).success).toBe(true);
    expect(LinkSchema.safeParse({ label: lock.repeat(121), href: "/dashboard" }).success).toBe(false);
  });
  it("accepts a 256-code-point control name", () => {
    const view = (sample as any).a2ui.messages.find((m: any) => m.updateDataModel).updateDataModel.value.views.control;
    expect(DATA_CONTRACTS.ControlSummary.safeParse({ ...view, name: lock.repeat(256) }).success).toBe(true);
    expect(DATA_CONTRACTS.ControlSummary.safeParse({ ...view, name: lock.repeat(257) }).success).toBe(false);
  });
});

describe("exception list truncation", () => {
  it("says how many exceptions are shown out of the total", async () => {
    const messages = JSON.parse(JSON.stringify((sample as any).a2ui.messages));
    const model = messages.find((m: any) => m.updateDataModel).updateDataModel.value.views;
    model.exceptions = { ...model.exceptions, total: 250, truncated: true };
    render(<A2uiCanvas messages={messages} onAction={vi.fn()} />);
    expect(await screen.findByTestId("ws-exceptions-truncated")).toHaveTextContent(
      `Showing ${model.exceptions.items.length} of 250 exceptions`);
  });
});

describe("workspace shell lifecycle", () => {
  it("does not touch the URL when an investigation finishes after the user has left", async () => {
    let release: (v: unknown) => void = () => undefined;
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/features") return { a2ui_workspace: { enabled: true }, workspace_ai: { available: false, reason: "off" } };
      if (path === "/me/preferences") return { experience: "workspace", effective_experience: "workspace", workspace_mode: "deterministic" };
      if (path === "/workspace/intents") return { intents: [], examples: [] };
      if (path === "/workspace/investigations") return new Promise((resolve) => { release = resolve; });
      throw new Error(`unexpected ${path}`);
    });
    nav.search = "q=slow+question";
    const { unmount } = render(<FeaturesProvider><WorkspacePage /></FeaturesProvider>);
    await vi.waitFor(() => expect(apiMock.mock.calls.some(([p]) => p === "/workspace/investigations")).toBe(true));
    unmount();
    await act(async () => { release({ ...sample, question: "slow question" }); });
    expect(nav.router.replace).not.toHaveBeenCalled();
  });
});
