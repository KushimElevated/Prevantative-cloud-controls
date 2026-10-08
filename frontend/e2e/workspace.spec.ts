import { expect, Page, test } from "@playwright/test";

// Optional AI Control Workspace, deterministic mode, against a reset/seeded backend with
// ENABLE_A2UI_WORKSPACE=true. Runs after journey.spec.ts (workers: 1) or on its own after a reset.

const QUESTION =
  "What would happen if we prevented public network access for all Azure AI Search services in production?";

async function loginAs(page: Page, username: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${username}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

async function investigate(page: Page, params: Record<string, string>) {
  await page.goto(`/workspace?${new URLSearchParams(params).toString()}`);
  await expect(page.getByTestId("ws-summary")).toBeVisible();
  await expect(page.getByTestId("ws-canvas-surface")).toBeVisible();
}

/** Runs the existing assessment from the canvas (with confirmation) when the scope has none yet. */
async function ensureAssessment(page: Page) {
  if (await page.getByTestId("ws-impact-unavailable").isVisible()) {
    await page.getByTestId("ws-run-assessment").click();
    await page.getByTestId("ws-confirm").click();
    await expect(page.getByTestId("ws-config-counts")).toBeVisible();
  }
}

test("experience selector, headline investigation, scope change and deep links", async ({ page }) => {
  await loginAs(page, "cara");
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(page.getByTestId("nav-workspace")).toBeVisible();

  await page.getByTestId("experience-workspace").click();
  await expect(page).toHaveURL(/\/workspace/);
  await expect(page.getByTestId("workspace-shell")).toBeVisible();

  await page.getByTestId("ws-example-0").click();
  await expect(page.getByTestId("ws-summary")).toContainText("CTL-AZ-SEARCH-PNA");
  // The URL records what was sent (here only the question), which reproduces the same investigation.
  await expect(page).toHaveURL(/q=What\+would\+happen/);
  await expect(page.getByTestId("ws-interpretation")).toContainText("az-mg-prod");
  for (const name of ["ControlSummary", "ScopeSelector", "ImpactAssessment", "ExceptionReview", "PolicyDiffViewer",
                      "RolloutTimeline", "ApprovalStatus", "GitOpsHandoffPreview", "EvidencePanel"]) {
    await expect(page.getByTestId(`ws-component-${name}`)).toBeVisible();
  }
  await expect(page.getByTestId("ws-findings")).toContainText("Verified evidence");
  await expect(page.getByTestId("ws-demo-data")).toBeVisible();
  await expect(page.getByTestId("ws-not-deployed")).toBeVisible();
  await ensureAssessment(page);
  await expect(page.getByTestId("ws-findings")).toContainText("Estimated impact");
  await expect(page.getByTestId("ws-component-ResourceImpactTable")).toContainText("srch-payments-kb");

  // Changing scope re-runs the read-only investigation and is reflected in the shareable URL.
  await page.getByTestId("ws-scope-select").selectOption("az-sub-retail-prod");
  await expect(page).toHaveURL(/scope=az-sub-retail-prod/);
  await expect(page.getByTestId("ws-summary")).toContainText("az-sub-retail-prod");
  await expect(page.getByTestId("ws-component-ResourceImpactTable")).not.toContainText("srch-payments-kb");

  // Deep links: workspace -> Classic -> workspace.
  await page.getByTestId("ws-open-classic").click();
  await expect(page).toHaveURL(/\/controls\/CTL-AZ-SEARCH-PNA/);
  await page.getByTestId("open-in-workspace").click();
  await expect(page).toHaveURL(/\/workspace\?.*control=CTL-AZ-SEARCH-PNA/);
  await expect(page.getByTestId("ws-component-ControlSummary")).toBeVisible();

  // The preference is persisted per user; Classic remains one click away.
  await page.goto("/");
  await expect(page).toHaveURL(/\/workspace/);
  await page.getByTestId("experience-classic").click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/dashboard/);
});

test("unknown impact is never estimated; the existing assessment runs only after confirmation", async ({ page }) => {
  await loginAs(page, "cara");
  await investigate(page, { q: "Is the AWS S3 public access control ready to roll out?" });
  await expect(page.getByTestId("ws-summary")).toContainText("CTL-AWS-S3-PUBLIC-ACCESS");
  await expect(page.getByTestId("ws-impact-unavailable")).toBeVisible();

  await page.getByTestId("ws-run-assessment").click();
  await expect(page.getByTestId("ws-confirm-scope")).toContainText("aws-root");
  await page.getByTestId("ws-cancel").click();
  await expect(page.getByTestId("ws-impact-unavailable")).toBeVisible();

  await page.getByTestId("ws-run-assessment").click();
  await page.getByTestId("ws-confirm").click();
  await expect(page.getByTestId("ws-config-counts")).toBeVisible();
  await expect(page.getByTestId("ws-impact-unavailable")).toHaveCount(0);

  // The run is the ordinary persisted, audited assessment visible in Classic.
  await page.goto("/assessments");
  await expect(page.getByText("CTL-AWS-S3-PUBLIC-ACCESS").first()).toBeVisible();
});

test("scoped requester sees only authorised data and drafts an exception through the governed workflow",
  async ({ page }) => {
    await loginAs(page, "cara");
    await investigate(page, { q: QUESTION });
    await ensureAssessment(page);

    await loginAs(page, "riley");
    await investigate(page, { q: QUESTION });
    await expect(page.getByTestId("ws-summary")).toContainText("az-sub-retail-prod");
    await expect(page.locator("main")).not.toContainText(/payments/i);
    await expect(page.getByTestId("ws-action-run_assessment")).toBeDisabled();

    await page.getByTestId("ws-prepare-exception-srch-retail-partner").click();
    const draft = page.getByTestId("ws-draft");
    await expect(draft).toBeVisible();
    await expect(page.getByTestId("ws-draft-submit")).toBeDisabled();
    await page.getByTestId("ws-draft-field-business_justification").fill(
      "Partner integration needs public access until the private link migration completes.");
    await page.getByTestId("ws-draft-field-technical_justification").fill(
      "Partner API calls originate outside our network; IP rules restrict callers meanwhile.");
    await page.getByTestId("ws-draft-field-risk_owner").fill("Retail platform owner");
    await page.getByTestId("ws-draft-field-compensating_controls").fill("IP allow-list\nQuery key rotation");
    await page.getByTestId("ws-draft-submit").click();
    await page.getByTestId("ws-confirm").click();
    await expect(page.getByTestId("ws-draft-created")).toBeVisible();
    await page.getByTestId("ws-draft-created").getByRole("link").click();
    await expect(page).toHaveURL(/\/exceptions\/exc/);
    await expect(page.getByText("Requested").first()).toBeVisible();
  });
