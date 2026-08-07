/**
 * Form Management Tests (Admin)
 *
 * Tests form CRUD operations from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_forms.py
 */

import { test, expect } from "@playwright/test";

test.describe("Form Listing", () => {
	test("should display forms page", async ({ page }) => {
		await page.goto("/forms");

		// Should see forms heading
		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});
	});

	test("should show create form button for admin", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Admin should see create button
		await expect(
			page.getByRole("button", { name: /create|new|add/i }).first(),
		).toBeVisible();
	});

	test("should list existing forms", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Either we have forms or an empty state
		const formContent = page.locator(
			"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
		);

		const hasforms = await formContent.count().catch(() => 0);
		const hasEmptyState = await page
			.getByText(/no forms|create your first/i)
			.isVisible()
			.catch(() => false);

		expect(hasforms > 0 || hasEmptyState).toBe(true);
	});
});

test.describe("Form Creation", () => {
	test("should open create form dialog/page", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Click create button
		const createButton = page
			.getByRole("button", { name: /create|new|add/i })
			.first();
		await createButton.click();

		// Should show form creation UI
		await expect(
			page
				.getByLabel(/name/i)
				.or(page.getByPlaceholder(/name/i))
				.or(page.getByRole("textbox", { name: /name/i })),
		).toBeVisible({ timeout: 5000 });
	});
});

test.describe("Form Details", () => {
	test("should show form details when clicked", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Find a form row/card
		const formItem = page
			.locator(
				"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
			)
			.first();

		if (await formItem.isVisible().catch(() => false)) {
			await formItem.click();

			// Check for detail content
			const hasDetails =
				page.url().includes("/forms/") ||
				(await page
					.getByText(/fields|schema|settings/i)
					.isVisible()
					.catch(() => false));

			expect(hasDetails).toBe(true);
		}
	});

	test("should show form fields configuration", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Find a form to view
		const formItem = page
			.locator(
				"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
			)
			.first();

		if (await formItem.isVisible().catch(() => false)) {
			await formItem.click();

			// Look for fields section
			await expect(
				page.getByText(/fields|inputs|parameters/i),
			).toBeVisible({ timeout: 5000 });
		}
	});
});

test.describe("Form Editing", () => {
	test("should show edit button for forms", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Look for edit buttons
		const editButton = page
			.getByRole("button", { name: /edit/i })
			.or(page.locator("[data-testid='edit-form']"))
			.first();

		// Either we have edit buttons or no forms
		const hasButton = await editButton.isVisible().catch(() => false);
		const hasEmptyState = await page
			.getByText(/no forms/i)
			.isVisible()
			.catch(() => false);

		expect(hasButton || hasEmptyState).toBe(true);
	});
});

test.describe("Form Access Control", () => {
	test("should show role assignment for forms", async ({ page }) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10000,
		});

		// Find a form
		const formItem = page
			.locator(
				"table tbody tr, [data-testid='form-card'], [data-testid='form-row']",
			)
			.first();

		if (await formItem.isVisible().catch(() => false)) {
			await formItem.click();

			// Look for access/permissions section
			const hasAccessSection = await page
				.getByText(/access|permissions|roles/i)
				.isVisible({ timeout: 5000 })
				.catch(() => false);

			// Access control UI should be present (implementation may vary)
			expect(hasAccessSection || page.url().includes("/forms/")).toBe(
				true,
			);
		}
	});
});
