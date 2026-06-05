/**
 * Live verification of the Solutions app experience against a running debug
 * stack (port mode). Self-contained: logs in through the REAL UI (no setup
 * project), then drives the authenticated surfaces and captures screenshots.
 *
 *   TEST_BASE_URL=http://localhost:<port> npx playwright test \
 *     e2e/solutions-live-verify.unauth.spec.ts --project=unauthenticated
 *
 * Manual verification harness — not CI. The .unauth project starts with a
 * clean browser (no storageState), which is what we want: we exercise login.
 */
import { test, expect } from "@playwright/test";

const SHOTS = "test-results/solutions-live";

test("real login flow works", async ({ page }) => {
  await page.goto("/login");
  await page.fill("#email", "dev@gobifrost.com");
  await page.fill("#password", "password");
  await page.screenshot({ path: `${SHOTS}/00-login.png` });
  await page.click('button[type="submit"]');
  // After login we should leave /login and land in the authenticated app.
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 20000,
  });
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/01-dashboard.png`, fullPage: true });
  expect(page.url()).not.toContain("/login");
});

test("authenticated surfaces render (apps, workflows)", async ({ page }) => {
  // Log in first (each test gets a fresh context in the unauth project).
  await page.goto("/login");
  await page.fill("#email", "dev@gobifrost.com");
  await page.fill("#password", "password");
  await page.click('button[type="submit"]');
  await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
    timeout: 20000,
  });

  await page.goto("/apps");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/02-apps-list.png`, fullPage: true });
  await expect(page.locator("body")).toBeVisible();

  await page.goto("/workflows");
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: `${SHOTS}/03-workflows.png`, fullPage: true });
  await expect(page.locator("body")).toBeVisible();
});
