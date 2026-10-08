import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { FeaturesProvider, useFeatures } from "@/lib/features";
import type { Draft, Investigation } from "@/lib/workspace";
import { ExperienceSelector } from "@/components/ExperienceSelector";
import { InvestigationPanel } from "@/components/workspace/InvestigationPanel";
import { ConfirmDialog } from "@/components/workspace/ConfirmDialog";
import { DraftPanel } from "@/components/workspace/drafts/DraftPanel";
import { ErrorBoundary } from "@/components/workspace/ErrorBoundary";
import sample from "../fixtures/investigation-sample.json";

const apiMock = vi.hoisted(() => vi.fn());
const nav = vi.hoisted(() => ({ router: { push: vi.fn(), replace: vi.fn() }, pathname: "/dashboard" }));

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: apiMock }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({ user: { user_id: "u-cara", username: "cara", display_name: "Cara", team: "Security", grants: [] }, ready: true }),
}));

const investigation = sample as unknown as Investigation;
const PREFS = { experience: "classic", effective_experience: "classic", workspace_mode: "deterministic" };
const AI_OFF = { available: false, reason: "AI-assisted mode is not configured (WORKSPACE_AI_PROVIDER=none).", model: null };

function serve(enabled: boolean | "error") {
  apiMock.mockImplementation(async (path: string, init?: { method?: string; body?: any }) => {
    if (path === "/features") {
      if (enabled === "error") throw new ApiError(500, "ERROR", "boom");
      return { a2ui_workspace: { enabled }, workspace_ai: { available: false, reason: "off" } };
    }
    if (path === "/me/preferences" && init?.method === "PUT") {
      return { ...init.body, effective_experience: init.body.experience };
    }
    if (path === "/me/preferences") return PREFS;
    throw new Error(`unexpected call ${path}`);
  });
}

function Probe() {
  const f = useFeatures();
  return <span data-testid="probe">{f.loading ? "loading" : `ready:${f.workspaceEnabled}`}</span>;
}

beforeEach(() => {
  apiMock.mockReset();
  nav.router.push.mockReset();
  nav.router.replace.mockReset();
  nav.pathname = "/dashboard";
});

describe("ExperienceSelector", () => {
  it("is not rendered when the workspace is disabled", async () => {
    serve(false);
    render(<FeaturesProvider><Probe /><ExperienceSelector /></FeaturesProvider>);
    expect(await screen.findByText("ready:false")).toBeInTheDocument();
    expect(screen.queryByTestId("experience-classic")).toBeNull();
    expect(screen.queryByTestId("experience-workspace")).toBeNull();
  });

  it("treats a failing /features call as disabled so Classic keeps working", async () => {
    serve("error");
    render(<FeaturesProvider><Probe /><ExperienceSelector /></FeaturesProvider>);
    expect(await screen.findByText("ready:false")).toBeInTheDocument();
    expect(screen.queryByTestId("experience-workspace")).toBeNull();
  });

  it("persists the choice and then navigates", async () => {
    serve(true);
    render(<FeaturesProvider><ExperienceSelector /></FeaturesProvider>);
    const ws = await screen.findByTestId("experience-workspace");
    expect(screen.getByTestId("experience-classic")).toHaveAttribute("aria-pressed", "true");
    expect(ws).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(ws);
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/workspace"));
    expect(apiMock).toHaveBeenCalledWith("/me/preferences", {
      method: "PUT", body: { experience: "workspace", workspace_mode: "deterministic" },
    });
    const put = apiMock.mock.invocationCallOrder[apiMock.mock.calls.findIndex(([, i]) => i?.method === "PUT")];
    expect(put).toBeLessThan(nav.router.push.mock.invocationCallOrder[0]);
  });

  it("goes back to the Classic dashboard from the workspace", async () => {
    serve(true);
    nav.pathname = "/workspace";
    render(<FeaturesProvider><ExperienceSelector /></FeaturesProvider>);
    fireEvent.click(await screen.findByTestId("experience-classic"));
    await waitFor(() => expect(nav.router.push).toHaveBeenCalledWith("/dashboard"));
    expect(apiMock).toHaveBeenCalledWith("/me/preferences", {
      method: "PUT", body: { experience: "classic", workspace_mode: "deterministic" },
    });
  });
});

function renderPanel(inv: Investigation | null, ai: { available: boolean; reason: string | null; model: string | null } = AI_OFF) {
  return render(
    <InvestigationPanel question="" onQuestionChange={vi.fn()} onSubmit={vi.fn()} examples={["Example one?"]}
                        onExample={vi.fn()} mode="deterministic" onModeChange={vi.fn()} ai={ai} pinned={{}}
                        onClearPin={vi.fn()} running={false} error={null} investigation={inv} />,
  );
}

describe("InvestigationPanel", () => {
  it("groups the fixture findings by kind with labels and a platform summary badge", () => {
    renderPanel(investigation);
    const byKind = (k: string) => investigation.findings.filter((f) => f.kind === k).length;
    const groups: [string, string][] = [["EVIDENCE", "Verified evidence"], ["ESTIMATE", "Estimated impact"],
      ["UNKNOWN", "Unknown"], ["RECOMMENDATION", "Recommendation"]];
    for (const [kind, label] of groups) {
      const group = screen.getByTestId(`ws-findings-${kind}`);
      expect(within(group).getByRole("heading", { name: label })).toBeInTheDocument();
      expect(within(group).getAllByRole("listitem")).toHaveLength(byKind(kind));
    }
    expect(within(screen.getByTestId("ws-findings-RECOMMENDATION")).getByText("Platform")).toBeInTheDocument();

    const summary = screen.getByTestId("ws-summary");
    expect(summary).toHaveTextContent("Platform (rule-based)");
    expect(summary).toHaveTextContent(investigation.summary.text);
    expect(summary).not.toHaveTextContent("AI-generated");

    const sourceLinks = within(screen.getByTestId("ws-findings")).getAllByRole("link");
    expect(sourceLinks[0]).toHaveAttribute("href", "/controls/CTL-AZ-SEARCH-PNA");
    expect(screen.getByTestId("ws-steps")).toHaveTextContent("GetImpactAssessment");
  });

  it("labels AI summaries and recommendations and never links to external hrefs", () => {
    renderPanel({
      ...investigation,
      mode: "ai",
      summary: { text: "Three services would be blocked.", origin: "ai", model: "claude-opus-5-5", label: "AI-generated summary." },
      findings: [
        ...investigation.findings,
        { kind: "RECOMMENDATION", text: "Pilot on retail first.", origin: "ai", source: null },
        { kind: "EVIDENCE", text: "Tampered source.", origin: "platform", source: { type: "x", id: "x", href: "https://evil.example/x" } },
      ],
    });
    const summary = screen.getByTestId("ws-summary");
    expect(summary).toHaveTextContent("AI-generated — verify against the canvas");
    expect(summary).toHaveTextContent("claude-opus-5-5");
    expect(summary).not.toHaveTextContent("Platform (rule-based)");
    const recs = screen.getByTestId("ws-findings-RECOMMENDATION");
    expect(within(recs).getByText("Pilot on retail first.").closest("li")).toHaveTextContent(/^AI/);
    const tampered = screen.getByText(/Tampered source/).closest("li")!;
    expect(within(tampered).queryByRole("link")).toBeNull();
  });

  it("disables AI-assisted mode with the server's reason when it is unavailable", () => {
    renderPanel(null);
    const aiButton = screen.getByTestId("ws-mode-ai");
    expect(aiButton).toBeDisabled();
    expect(screen.getByText(AI_OFF.reason)).toBeInTheDocument();
    expect(aiButton).toHaveAccessibleDescription(AI_OFF.reason);
    expect(screen.getByTestId("ws-mode-deterministic")).toHaveAttribute("aria-pressed", "true");
  });

  it("enables AI-assisted mode when the server reports it available", () => {
    renderPanel(null, { available: true, reason: null, model: "claude-opus-5-5" });
    expect(screen.getByTestId("ws-mode-ai")).toBeEnabled();
  });
});

describe("ConfirmDialog", () => {
  it("is a labelled modal that takes focus, cancels on Escape and confirms explicitly", () => {
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(<ConfirmDialog title="Run impact assessment?" onConfirm={onConfirm} onCancel={onCancel}>Details</ConfirmDialog>);
    const dialog = screen.getByRole("dialog", { name: "Run impact assessment?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog.contains(document.activeElement)).toBe(true);

    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("ws-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(2);
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("ws-confirm"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});

const exceptionDraft: Draft = {
  kind: "exception",
  title: "Exception request for srch-payments-legacy",
  basis: { control_revision_id: "crev-1", control_revision_digest: "sha256:aaa" },
  payload: {
    control_id: "CTL-AZ-SEARCH-PNA", implementation_id: "impl-1", application: "payments-legacy", team: "Payments",
    business_justification: "", technical_justification: "", scope_id: "az-sub-payments-prod", granularity: "RESOURCE",
    resource_ids: ["/subscriptions/x/srch-payments-legacy"], principal_patterns: [], risk_owner: "",
    compensating_controls: [], valid_from: null, expires_at: "2026-11-07T12:00:00Z", work_ref: null,
  },
  required_fields: ["business_justification", "technical_justification", "risk_owner", "compensating_controls"],
  can_submit: true,
  blocking_reasons: [],
  preview: { representability: "REPRESENTABLE", reason: "Exemption per assignment.", native_expiry_supported: true },
  notes: ["Justifications must be written by a person."],
  submit_via: "POST /api/v1/workspace/drafts/submit",
};

describe("DraftPanel", () => {
  it("only submits after review and confirmation, and offers to prepare a stale draft again", async () => {
    apiMock.mockRejectedValue(new ApiError(409, "DRAFT_STALE", "The draft is stale: the platform state it was prepared from has changed.",
      { changed: ["control_revision_digest"], current_basis: {} }));
    const onPrepareAgain = vi.fn();
    render(<DraftPanel draft={exceptionDraft} onPrepareAgain={onPrepareAgain} onDiscard={vi.fn()} />);

    const submit = screen.getByTestId("ws-draft-submit");
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("ws-draft-field-business_justification"), { target: { value: "Partner integration needs public access until Q4." } });
    fireEvent.change(screen.getByTestId("ws-draft-field-technical_justification"), { target: { value: "Private endpoint migration is scheduled." } });
    fireEvent.change(screen.getByTestId("ws-draft-field-risk_owner"), { target: { value: "Payments CISO" } });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByTestId("ws-draft-field-compensating_controls"), { target: { value: "IP allow-list\n\nWAF in front\n" } });
    expect(submit).toBeEnabled();

    fireEvent.click(submit);
    expect(apiMock).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByTestId("ws-confirm"));
    });

    expect(apiMock).toHaveBeenCalledWith("/workspace/drafts/submit", {
      method: "POST",
      body: {
        kind: "exception", basis: exceptionDraft.basis, confirmed: true,
        payload: expect.objectContaining({
          risk_owner: "Payments CISO", compensating_controls: ["IP allow-list", "WAF in front"],
          expires_at: "2026-11-07T12:00:00Z", scope_id: "az-sub-payments-prod",
        }),
      },
    });
    const stale = await screen.findByTestId("ws-draft-stale");
    expect(stale).toHaveTextContent("This draft is stale");
    expect(stale).toHaveTextContent("control_revision_digest");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("ws-draft-submit")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Prepare again" }));
    expect(onPrepareAgain).toHaveBeenCalledTimes(1);
  });

  it("keeps submission disabled when the server reports blocking reasons", () => {
    render(<DraftPanel draft={{ ...exceptionDraft, can_submit: false, blocking_reasons: ["Requires EXCEPTION_REQUESTER."] }}
                       onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.getByTestId("ws-draft-blocking")).toHaveTextContent("Requires EXCEPTION_REQUESTER.");
    expect(screen.getByTestId("ws-draft-submit")).toBeDisabled();
  });

  it("links to the created object after a confirmed submission", async () => {
    apiMock.mockResolvedValue({ created: { type: "control", id: "CTL-AZ-NEW", href: "/controls/CTL-AZ-NEW" }, note: "Created." });
    const draft: Draft = {
      ...exceptionDraft, kind: "control", title: "New control proposal", preview: null,
      required_fields: ["id", "name", "security_objective", "rationale", "severity", "applicability_criteria",
                        "security_owner", "engineering_owner", "prevention_boundary"],
      payload: { id: "CTL-AZ-NEW", name: "n", description: "d", security_objective: "o", rationale: "r", severity: "HIGH",
        providers: ["azure"], resource_types: ["Microsoft.Search/searchServices"], applicability_criteria: "a",
        security_owner: "s", engineering_owner: "e", prevention_boundary: "b" },
    };
    render(<DraftPanel draft={draft} onPrepareAgain={vi.fn()} onDiscard={vi.fn()} />);
    fireEvent.click(screen.getByTestId("ws-draft-submit"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("ws-confirm"));
    });
    const created = await screen.findByTestId("ws-draft-created");
    expect(within(created).getByRole("link", { name: "Open in the Classic Experience" })).toHaveAttribute("href", "/controls/CTL-AZ-NEW");
  });
});

describe("ErrorBoundary", () => {
  it("renders a fallback with a retry and a Classic link when a child throws", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    let fail = true;
    function Flaky() {
      if (fail) throw new Error("render failed");
      return <div>Recovered canvas</div>;
    }
    render(
      <div>
        <ErrorBoundary classicHref="/controls/CTL-AZ-SEARCH-PNA" what="canvas"><Flaky /></ErrorBoundary>
        <p>Sibling panel</p>
      </div>,
    );
    const fallback = screen.getByRole("alert");
    expect(fallback).toHaveTextContent("The canvas could not be displayed.");
    expect(within(fallback).getByRole("link", { name: "Open the Classic Experience" })).toHaveAttribute("href", "/controls/CTL-AZ-SEARCH-PNA");
    expect(screen.getByText("Sibling panel")).toBeInTheDocument();

    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("Recovered canvas")).toBeInTheDocument();
    error.mockRestore();
    warn.mockRestore();
  });
});
