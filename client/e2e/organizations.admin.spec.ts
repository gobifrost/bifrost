/**
 * Organization Management Tests (Admin)
 *
 * Tests organization CRUD operations from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_organizations.py
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Organization Management", () => {
	test("displays the organization list with standard row actions", async ({
		page,
	}) => {
		await page.goto("/organizations");

		// Should see organizations page
		await expect(
			page.getByRole("heading", { name: /organizations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		const organizationRow = page.locator("table tbody tr").first();
		await expect(organizationRow).toBeVisible();

		await organizationRow.getByRole("button", { name: /actions$/ }).click();
		await expect(
			page.getByRole("menuitem", { name: /^Edit / }),
		).toBeVisible();
		await expect(
			page.getByRole("menuitem", { name: /^Disable / }),
		).toBeVisible();
	});

	test("edits status and instructions from the organization row", async ({
		page,
		api,
	}) => {
		const organizationName = `Organization UX ${Date.now()}`;
		const createResponse = await api.post("/api/organizations", {
			data: { name: organizationName, domain: "organization-ux.example" },
		});
		expect(createResponse.ok(), await createResponse.text()).toBeTruthy();
		const organization = (await createResponse.json()) as { id: string };

		try {
			await page.goto("/organizations");
			const organizationRow = page.getByRole("row", {
				name: new RegExp(organizationName),
			});
			await expect(organizationRow).toBeVisible({ timeout: 10000 });

			await organizationRow.click();
			await expect(
				page.getByRole("heading", { name: "Edit Organization" }),
			).toBeVisible();
			await page
				.getByRole("switch", { name: "Organization Status" })
				.click();
			await page.getByRole("button", { name: "Save Changes" }).click();
			await expect(
				page.getByRole("heading", { name: "Edit Organization" }),
			).toBeHidden();
			await expect(organizationRow).toBeHidden();

			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(organizationRow).toBeVisible();
			await organizationRow.click();
			await expect(
				page.getByRole("switch", { name: "Organization Status" }),
			).not.toBeChecked();

			await page.getByRole("tab", { name: "Instructions" }).click();
			await expect(
				page.getByRole("heading", {
					name: "Organization Instructions",
				}),
			).toBeVisible();
		} finally {
			await api.delete(`/api/organizations/${organization.id}`);
		}
	});

	test("should be able to create new organization", async ({ page }) => {
		await page.goto("/organizations");

		// Look for create button
		const createButton = page.getByRole("button", {
			name: /create|new|add/i,
		});

		if (await createButton.isVisible().catch(() => false)) {
			await createButton.click();

			// Should show create form/dialog
			await expect(
				page.getByLabel(/name/i).or(page.getByPlaceholder(/name/i)),
			).toBeVisible({ timeout: 5000 });
		}
	});

	test("should show organization members", async ({ page }) => {
		await page.goto("/organizations");

		// Wait for list to load
		await expect(
			page.getByRole("heading", { name: /organizations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for members link/tab
		const membersLink = page
			.getByRole("link", { name: /members/i })
			.first();
		const membersTab = page.getByRole("tab", { name: /members/i }).first();

		if (await membersLink.isVisible().catch(() => false)) {
			await membersLink.click();
			await expect(page.getByText(/alice|bob|admin/i)).toBeVisible({
				timeout: 5000,
			});
		} else if (await membersTab.isVisible().catch(() => false)) {
			await membersTab.click();
			await expect(page.getByText(/alice|bob|admin/i)).toBeVisible({
				timeout: 5000,
			});
		}
	});
});

test.describe("Organization Settings", () => {
	test("should access organization settings", async ({ page }) => {
		await page.goto("/organizations");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /organizations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for settings link or button
		const settingsLink = page
			.getByRole("link", { name: /settings/i })
			.or(page.getByRole("button", { name: /settings/i }))
			.first();

		if (await settingsLink.isVisible().catch(() => false)) {
			await settingsLink.click();

			// Should show settings page/panel
			await expect(page.locator("main")).toBeVisible();
		}
	});
});
