/**
 * Solution lifecycle UI — status badge, show-inactive toggle, uninstall vs
 * hard-delete affordances (admin-only).
 *
 * These tests cover the STRUCTURAL wiring of the new lifecycle UI without
 * requiring a real Solution install fixture (which needs a built .zip + full
 * server-side deploy pipeline). Full lifecycle coverage (install → uninstall →
 * reactivate → hard-delete) lives in the API-layer E2E suite
 * (`api/tests/e2e/platform/test_solution_lifecycle.py`); vitest covers
 * component-level rendering and modal interaction. This Playwright spec covers
 * what is genuinely exercisable through the browser against an empty test
 * stack: the list surface's structural affordances and the absence of the
 * removed "show orphaned" toggles on Tables and Config.
 */

import { test, expect } from "@playwright/test";

test.describe("Solution lifecycle UI (admin)", () => {
	test("solutions list has show-inactive toggle", async ({ page }) => {
		await page.goto("/solutions");
		await expect(
			page.getByRole("heading", { name: "Solutions", exact: true }),
		).toBeVisible({ timeout: 10000 });

		// Show-inactive toggle must be present on the list page.
		await expect(
			page.locator('[data-testid="show-inactive-toggle"]'),
		).toBeVisible();
	});

	test("Tables page has no show-orphaned toggle (stripped)", async ({
		page,
	}) => {
		await page.goto("/tables");
		await expect(
			page.getByRole("heading", { name: /data tables/i }),
		).toBeVisible({ timeout: 10000 });

		// The "Show orphaned" checkbox must no longer exist.
		await expect(
			page.getByRole("checkbox", { name: /show orphaned/i }),
		).not.toBeVisible();
	});

	test("Config page has no show-orphaned toggle (stripped)", async ({
		page,
	}) => {
		await page.goto("/config");
		await expect(
			page.getByRole("heading", { name: /configuration/i }),
		).toBeVisible({ timeout: 10000 });

		// The "Show orphaned" checkbox must no longer exist.
		await expect(
			page.getByRole("checkbox", { name: /show orphaned/i }),
		).not.toBeVisible();
	});
});
