/**
 * Permission Tests (Org User)
 *
 * Org users can load their workspace shell, but platform-admin routes must
 * deny in place and recover without forcing reauthentication.
 *
 * Forms assignment, forbidden forms, and member form launch are covered in
 * `forms.user.spec.ts`. Execution history behavior is covered by the execution
 * specs. Removed heading-only checks and the placeholder "own organization
 * data only" test because they did not prove a permission boundary.
 */

import { test, expect } from "@playwright/test";

test.describe("Org user denied route recovery", () => {
	test("denies /config in place, then returns to a non-admin Home", async ({
		page,
	}) => {
		await page.goto("/config");

		await expect(
			page.getByRole("heading", {
				name: "You don’t have access",
				exact: true,
			}),
		).toBeVisible({ timeout: 10000 });
		await expect(page).toHaveURL(/\/config$/);

		await page.getByRole("button", { name: "Go to Home" }).click();

		await expect(page).toHaveURL(/\/$/);
		await expect(
			page.getByRole("heading", {
				name: "Workspace",
				exact: true,
				level: 1,
			}),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByRole("navigation", { name: "Workspace views" }),
		).toHaveCount(0);

		const primaryNavigation = page.getByRole("navigation", {
			name: "Primary navigation",
		});
		await expect(primaryNavigation).toBeVisible();
		await expect(
			primaryNavigation.getByRole("link", {
				name: "Config",
				exact: true,
			}),
		).toHaveCount(0);
		await expect(
			primaryNavigation.getByRole("link", {
				name: "Organizations",
				exact: true,
			}),
		).toHaveCount(0);
	});
});
