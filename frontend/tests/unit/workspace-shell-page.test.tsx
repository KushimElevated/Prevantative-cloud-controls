import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { FeaturesProvider } from "@/lib/features";
import { AppShell } from "@/components/AppShell";
import WorkspacePage from "@/app/workspace/page";
import Home from "@/app/page";
import sample from "../fixtures/investigation-sample.json";

const apiMock = vi.hoisted(() => vi.fn());
const nav = vi.hoisted(() => ({ router: { push: vi.fn(), replace: vi.fn() }, pathname: "/workspace", search: "" }));

vi.mock("@/lib/api", async (importOriginal) => ({ ...(await importOriginal<typeof import("@/lib/api")>()), api: apiMock }));
vi.mock("next/navigation", () => ({
  useRouter: () => nav.router,
  usePathname: () => nav.pathname,
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
vi.mock("@/lib/session", () => ({
  useSession: () => ({
    user: { user_id: "u-cara", username: "cara", display_name: "Cara", team: "Security", grants: [{ role: "CONTROL_ENGINEER", scope_ids: ["*"] }] },
    ready: true, logout: vi.fn(),
  }),
}));
// The real canvas is covered by the catalog tests; here it is a stub that can emit canvas actions.
vi.mock("next/dynamic", () => ({
  default: () => function CanvasStub({ messages, onAction }: any) {
    const emit = (name: string, context: Record<string, string>) => onAction({ name, context, sourceComponentId: "c_stub" });
    return (
      <div data-testid="canvas-stub">
        {messages.length} messages
        <button onClick={() => emit("ws.select_scope", { scope_id: "az-sub-retail-prod" })}>select scope</button>
        <button onClick={() => emit("ws.run_assessment", { implementation_revision_id: "irev-az-search-pna-1", target_scope_id: "az-sub-retail-prod" })}>assess</button>
        <button onClick={() => emit("ws.prepare_exception_draft", { control_id: "CTL-AZ-SEARCH-PNA", resource_id: "/subscriptions/x/srch-1" })}>exception</button>
      </div>
    );
  },
}));

const PREFS = { experience: "workspace", effective_experience: "workspace", workspace_mode: "deterministic" };

function serve(enabled: boolean) {
  apiMock.mockImplementation(async (path: string, init?: { method?: string; body?: any }) => {
    if (path === "/features") {
      return { a2ui_workspace: { enabled }, workspace_ai: { available: false, reason: "AI is not configured." } };
    }
    if (path === "/me/preferences") return enabled ? PREFS : { ...PREFS, effective_experience: "classic" };
    if (path === "/workspace/intents") return { intents: [], examples: ["Which exceptions exist?"] };
    if (path === "/workspace/investigations") return { ...sample, question: init!.body.question, investigation_id: `inv-${apiMock.mock.calls.length}` };
    if (path === "/assessments") return { id: "asmt_new", status: "COMPLETED" };
    if (path === "/workspace/drafts/prepare") {
      return { kind: "exception", title: "Exception request for srch-1", basis: {}, payload: { expires_at: "2026-11-07T00:00:00Z" },
               required_fields: [], can_submit: false, blocking_reasons: ["Requires EXCEPTION_REQUESTER."], preview: null,
               notes: [], submit_via: "POST /api/v1/workspace/drafts/submit" };
    }
    throw new Error(`unexpected call ${path}`);
  });
}

const calls = (path: string) => apiMock.mock.calls.filter(([p]) => p === path);

function renderPage() {
  return render(<FeaturesProvider><WorkspacePage /></FeaturesProvider>);
}

beforeEach(() => {
  apiMock.mockReset();
  nav.router.push.mockReset();
  nav.router.replace.mockReset();
  nav.pathname = "/workspace";
  nav.search = "";
});

describe("workspace page", () => {
  it("shows a way back to Classic and makes no workspace calls when the feature is disabled", async () => {
    serve(false);
    renderPage();
    expect(await screen.findByTestId("ws-disabled")).toHaveTextContent("not enabled");
    expect(screen.getByRole("link", { name: "Go to the Classic Experience" })).toHaveAttribute("href", "/dashboard");
    expect(apiMock.mock.calls.filter(([p]) => String(p).startsWith("/workspace"))).toHaveLength(0);
  });

  it("investigates a deep-linked control on load and keeps the URL shareable", async () => {
    serve(true);
    nav.search = "control=CTL-AZ-SEARCH-PNA";
    renderPage();
    expect(await screen.findByTestId("canvas-stub")).toHaveTextContent(`${sample.a2ui.messages.length} messages`);
    expect(calls("/workspace/investigations")[0][1]).toEqual({
      method: "POST", body: { question: "Overview of CTL-AZ-SEARCH-PNA", mode: "deterministic", control_id: "CTL-AZ-SEARCH-PNA" },
    });
    expect(nav.router.replace).toHaveBeenCalledWith(
      "/workspace?q=Overview+of+CTL-AZ-SEARCH-PNA&control=CTL-AZ-SEARCH-PNA&mode=deterministic", { scroll: false });
    expect(screen.getByTestId("ws-question")).toHaveValue("Overview of CTL-AZ-SEARCH-PNA");
    expect(screen.getByTestId("ws-open-classic")).toHaveAttribute("href", "/controls/CTL-AZ-SEARCH-PNA");
    expect(screen.getByTestId("ws-mode-indicator")).toHaveTextContent("Deterministic");
    const context = screen.getByTestId("ws-context");
    expect(within(context).getByTestId("ws-action-run_assessment")).toBeEnabled();
    expect(within(context).getByRole("link", { name: "Audit history (Classic)" })).toHaveAttribute("href", "/audit?control_id=CTL-AZ-SEARCH-PNA");
  });

  it("asks before running an assessment through the existing endpoint, then refreshes", async () => {
    serve(true);
    nav.search = "q=What+would+happen&control=CTL-AZ-SEARCH-PNA&scope=az-mg-prod";
    renderPage();
    fireEvent.click(await screen.findByTestId("ws-action-run_assessment"));
    const dialog = screen.getByRole("dialog", { name: "Run impact assessment?" });
    expect(dialog).toHaveTextContent("Creates a persisted, audited assessment run");
    expect(within(dialog).getByTestId("ws-confirm-irev")).toHaveTextContent("irev-az-search-pna-1");
    expect(within(dialog).getByTestId("ws-confirm-scope")).toHaveTextContent("az-mg-prod");
    expect(calls("/assessments")).toHaveLength(0);

    fireEvent.click(within(dialog).getByTestId("ws-cancel"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls("/assessments")).toHaveLength(0);

    fireEvent.click(screen.getByTestId("ws-action-run_assessment"));
    await act(async () => {
      fireEvent.click(screen.getByTestId("ws-confirm"));
    });
    expect(calls("/assessments")[0][1]).toEqual({
      method: "POST", body: { implementation_revision_id: "irev-az-search-pna-1", target_scope_id: "az-mg-prod" },
    });
    await waitFor(() => expect(calls("/workspace/investigations")).toHaveLength(2));
    expect(calls("/workspace/investigations")[1][1].body).toMatchObject({
      question: "What would happen", control_id: "CTL-AZ-SEARCH-PNA", scope_id: "az-mg-prod",
    });
    expect(await screen.findByTestId("ws-notice")).toHaveTextContent("asmt_new");
  });

  it("routes canvas actions: scope selection re-runs, assessment confirms, exceptions prepare a draft", async () => {
    serve(true);
    nav.search = "q=What+would+happen";
    renderPage();
    await screen.findByTestId("canvas-stub");
    expect(calls("/workspace/investigations")[0][1].body).toEqual({ question: "What would happen", mode: "deterministic" });

    fireEvent.click(screen.getByRole("button", { name: "select scope" }));
    await waitFor(() => expect(calls("/workspace/investigations")).toHaveLength(2));
    expect(calls("/workspace/investigations")[1][1].body).toEqual({
      question: "What would happen", mode: "deterministic", control_id: "CTL-AZ-SEARCH-PNA", scope_id: "az-sub-retail-prod",
    });
    await waitFor(() => expect(nav.router.replace).toHaveBeenLastCalledWith(
      "/workspace?q=What+would+happen&control=CTL-AZ-SEARCH-PNA&scope=az-sub-retail-prod&mode=deterministic", { scroll: false }));

    fireEvent.click(await screen.findByRole("button", { name: "assess" }));
    expect(within(screen.getByRole("dialog")).getByTestId("ws-confirm-scope")).toHaveTextContent("az-sub-retail-prod");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "exception" }));
    await screen.findByText("Exception request for srch-1");
    const draft = screen.getByTestId("ws-draft");
    expect(calls("/workspace/drafts/prepare")[0][1]).toEqual({
      method: "POST", body: { kind: "exception", params: { control_id: "CTL-AZ-SEARCH-PNA", resource_id: "/subscriptions/x/srch-1" } },
    });
    expect(within(draft).getByTestId("ws-draft-submit")).toBeDisabled();
    expect(calls("/workspace/drafts/submit")).toHaveLength(0);
  });

  it("prepares a rollout draft from the context actions", async () => {
    serve(true);
    nav.search = "control=CTL-AZ-SEARCH-PNA";
    renderPage();
    fireEvent.click(await screen.findByTestId("ws-action-prepare_rollout_draft"));
    await waitFor(() => expect(calls("/workspace/drafts/prepare")).toHaveLength(1));
    expect(calls("/workspace/drafts/prepare")[0][1].body).toEqual({
      kind: "rollout_plan", params: { control_id: "CTL-AZ-SEARCH-PNA", implementation_revision_id: "irev-az-search-pna-1" },
    });
  });
});

describe("Classic integration", () => {
  it("adds the workspace entry and selector only when enabled", async () => {
    serve(true);
    nav.pathname = "/dashboard";
    const { unmount } = render(<FeaturesProvider><AppShell><p>Classic page</p></AppShell></FeaturesProvider>);
    const entry = await screen.findByTestId("nav-workspace");
    expect(entry).toHaveAttribute("href", "/workspace");
    expect(entry).toHaveTextContent("Optional");
    expect(screen.getByTestId("experience-classic")).toHaveAttribute("aria-pressed", "true");
    unmount();

    apiMock.mockReset();
    serve(false);
    render(<FeaturesProvider><AppShell><p>Classic page</p></AppShell></FeaturesProvider>);
    await waitFor(() => expect(calls("/me/preferences")).toHaveLength(1));
    await act(async () => undefined);
    expect(screen.getByText("Classic page")).toBeInTheDocument();
    expect(screen.queryByTestId("nav-workspace")).toBeNull();
    expect(screen.queryByTestId("experience-workspace")).toBeNull();
  });

  it("sends home to the preferred experience once features are known", async () => {
    serve(true);
    render(<FeaturesProvider><Home /></FeaturesProvider>);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/workspace"));
    expect(nav.router.replace).toHaveBeenCalledTimes(1);
  });

  it("sends home to the Classic dashboard when the workspace is disabled", async () => {
    serve(false);
    render(<FeaturesProvider><Home /></FeaturesProvider>);
    await waitFor(() => expect(nav.router.replace).toHaveBeenCalledWith("/dashboard"));
    expect(nav.router.replace).toHaveBeenCalledTimes(1);
  });
});
