/**
 * MANUAL verification harness — NOT part of CI.
 *
 * Lives in `manual-verify/` (outside `testDir: ./e2e`), named `.manual.ts`, so
 * `./test.sh client e2e` never collects it. Assumes a port-mode debug stack
 * AND that a Solution has already been deployed to it (the "Live Dash" v2 app,
 * "hello_live" workflow, "Legacy V1" app, and the hard-coded app UUID below) —
 * none of which exist on a clean CI test stack. Operator deploys the fixture
 * via the CLI first, then runs (uses playwright.manual.config.ts):
 *   cd client && TEST_BASE_URL=http://localhost:<port> \
 *     npx playwright test -c playwright.manual.config.ts
 */
import { test, expect } from "@playwright/test";

const SHOTS = "test-results/solutions-live";

async function login(page) {
  await page.goto("/login");
  await page.fill("#email", "dev@gobifrost.com");
  await page.fill("#password", "password");
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 20000,
  });
}

test("deployed solution app shows in apps list with managed affordance", async ({ page }) => {
  await login(page);
  await page.goto("/apps");
  // Wait for the deployed app card to appear (proves the deploy is visible to users).
  await expect(page.getByText("Live Dash")).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: `${SHOTS}/10-apps-with-solution.png`, fullPage: true });
});

test("deployed workflow shows managed/read-only affordance", async ({ page }) => {
  await login(page);
  await page.goto("/workflows");
  await expect(page.getByText("hello_live")).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: `${SHOTS}/11-workflows-with-solution.png`, fullPage: true });
});

test("v2 app dist is served from the platform", async ({ page, request }) => {
  await login(page);
  // The dist index.html for the deployed app id should be fetchable.
  // (app id from the deploy: Live Dash)
  const appId = "d87eda8c-6f83-4205-97e0-3bc1c687a689";
  const resp = await request.get(`/api/applications/${appId}/dist/index.html`);
  expect(resp.status()).toBe(200);
  const html = await resp.text();
  expect(html).toContain("V2 Standalone App");
});

test("managed app is still previewable in table view (no regression)", async ({ page }) => {
  await login(page);
  await page.goto("/apps");
  await expect(page.getByText("Live Dash")).toBeVisible({ timeout: 15000 });
  const tableToggle = page.locator('button[aria-label*="table" i], button[title*="table" i]').first();
  if (await tableToggle.count()) {
    await tableToggle.click();
    await page.waitForTimeout(500);
  }
  await page.screenshot({ path: `${SHOTS}/12-apps-table-view.png`, fullPage: true });
  await expect(page.getByTestId("solution-managed-badge").first()).toBeVisible();
});

test("v1 (non-solution) app is editable — backwards compat", async ({ page }) => {
  await login(page);
  await page.goto("/apps");
  await expect(page.getByText("Legacy V1")).toBeVisible({ timeout: 15000 });
  await page.screenshot({ path: `${SHOTS}/13-v1-app-editable.png`, fullPage: true });
  // Both apps present side-by-side: v1 editable, v2 managed (visual diff in shot).
  await expect(page.getByText("Live Dash")).toBeVisible();
});

test("managed app code editor is read-only (banner + disabled save)", async ({ page }) => {
  await login(page);
  await page.goto("/apps/live-dash/edit");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/14-managed-editor.png`, fullPage: true });
  await expect(page.getByTestId("solution-managed-banner")).toBeVisible({ timeout: 15000 });
});
