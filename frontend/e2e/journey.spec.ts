import { expect, Page, test } from "@playwright/test";

// Main journey through the UI against a freshly reset/seeded backend:
// proposal -> validation -> assessment -> blocker resolved -> plan -> package -> two distinct approvals
// -> approved export -> rollout advance -> mock pipeline -> fixture observation -> VERIFIED.
// Requires: `python -m app.cli reset` (or `make reset`) immediately before running.

const CONTROL = "/controls/CTL-AZ-SEARCH-PNA";

async function loginAs(page: Page, username: string) {
  await page.goto("/login");
  await page.getByTestId(`login-${username}`).click();
  await expect(page.getByTestId("current-user")).toBeVisible();
}

test("Azure AI Search control: proposal to mock-verified observation", async ({ page }) => {
  page.on("dialog", (d) => d.accept(`E2E rationale for ${d.message().slice(0, 40)}`));

  await loginAs(page, "cara");
  await page.goto(CONTROL);
  await expect(page.getByTestId("next-decision")).toContainText("Submit control revision");
  await page.getByTestId("submit-control").click();
  await expect(page.getByText("Revision 1: In review")).toBeVisible();

  await page.getByTestId("validate-impl-az-search-pna").click();
  await expect(page.getByTestId("impl-impl-az-search-pna")).toContainText("evaluator azure.search.public-network-access");
  await page.getByTestId("submit-impl-impl-az-search-pna").click();
  await expect(page.getByTestId("impl-impl-az-search-pna")).toContainText("r1 In review");

  await page.getByTestId("run-assessment").click();
  await expect(page.getByTestId("count-COMPLIANT")).toContainText("1");
  await expect(page.getByTestId("count-NON_COMPLIANT")).toContainText("5");
  await expect(page.getByTestId("count-UNKNOWN")).toContainText("1");
  await expect(page.getByTestId("count-NOT_APPLICABLE")).toContainText("1");

  // Resolve the pilot readiness blocker with supplied evidence, then reassess.
  await page.getByTestId("evidence-form").locator("summary").click();
  await page.getByTestId("ev-app").fill("retail-catalog");
  await page.getByTestId("ev-scope").selectOption("az-sub-retail-prod");
  await page.getByTestId("ev-prereq").selectOption("CLIENT_CONNECTIVITY");
  await page.getByTestId("ev-summary").fill("Validated storefront against the private endpoint");
  await page.getByTestId("ev-save").click();
  await page.getByTestId("run-assessment").click();
  await expect(page.getByRole("row", { name: "retail-catalog Ready" })).toBeVisible();

  await expect(page.getByTestId("pilot-scope")).toHaveValue("az-sub-retail-prod");
  await page.getByTestId("create-plan-btn").click();
  await page.getByTestId("submit-package").click();
  await expect(page.getByTestId("package-1").getByTestId("package-status")).toContainText("In review");

  // Security approval (Sam), then Cloud Engineering approval (Eli): distinct identities.
  await loginAs(page, "sam");
  await page.goto(CONTROL);
  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("package-1")).toContainText("Security approver by u-sam");

  await loginAs(page, "eli");
  await page.goto(CONTROL);
  await page.getByTestId("approve-package").click();
  await expect(page.getByTestId("package-1").getByTestId("package-status")).toContainText("Approved");

  await page.getByTestId("export-bundle").click();
  await expect(page.getByTestId("package-1")).toContainText("Approved handoff");
  await page.getByTestId("advance-rollout").click();
  await expect(page.getByTestId("plan-stage")).toContainText("Observation");
  await page.getByTestId("advance-rollout").click();
  await expect(page.getByTestId("plan-stage")).toContainText("Pilot");

  await page.getByTestId("run-pipeline").click();
  await expect(page.getByTestId("target-state-AZURE_POLICY_ASSIGNMENT")).toContainText("Applied");
  await page.getByTestId("observe-AZURE_POLICY_ASSIGNMENT").click();
  await expect(page.getByTestId("target-state-AZURE_POLICY_ASSIGNMENT")).toContainText("Verified");
  await page.getByTestId("observe-AZURE_POLICY_EXEMPTION").click();
  await expect(page.getByTestId("target-state-AZURE_POLICY_EXEMPTION")).toContainText("Verified");

  await expect(page.getByTestId("coverage-verified")).toContainText("1/7");
  await page.goto("/dashboard");
  await expect(page.getByTestId("tile-verified")).toContainText("/");
  await page.goto("/audit?control_id=CTL-AZ-SEARCH-PNA");
  await expect(page.getByText("handoff.exported")).toBeVisible();
});
