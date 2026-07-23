/**
 * Workflow Management Tests (Admin)
 *
 * Tests workflow listing, viewing, and execution from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_workflows.py
 */

import { test, expect } from "@playwright/test";

test.describe("Workflow Listing", () => {
	test("should display workflows page", async ({ page }) => {
		await page.goto("/workflows");

		// Should see workflows heading (use first() in case dashboard sidebar also shows "Workflows")
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Should see workflow list/table
		await expect(page.locator("main")).toBeVisible();
	});

	test("should show workflow cards or table rows", async ({ page }) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Each workflow (grid or table view) has an Execute / Test Tool / Preview
		// Data button. Count those instead of relying on data-testids that don't exist.
		const actionButtons = page.getByRole("button", {
			name: /execute workflow|test tool|preview data/i,
		});
		const emptyState = page.getByText(
			/no workflows available|no workflows match/i,
		);

		// The heading renders before the workflow query settles. Wait for the
		// loaded list or its empty state instead of sampling during skeletons.
		await expect(actionButtons.first().or(emptyState)).toBeVisible({
			timeout: 10000,
		});
	});

	test("should show workflow details when clicked", async ({ page }) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find a workflow row/card
		const workflowItem = page
			.locator(
				"table tbody tr, [data-testid='workflow-card'], [data-testid='workflow-row']",
			)
			.first();

		if (await workflowItem.isVisible().catch(() => false)) {
			await workflowItem.click();

			// Check if we're on a detail page or showing details
			const hasDetails =
				page.url().includes("/workflows/") ||
				(await page
					.getByText(/parameters|inputs|description/i)
					.isVisible()
					.catch(() => false));

			expect(hasDetails).toBe(true);
		}
	});
});

test.describe("Workflow Execution", () => {
	test("should show execute button on workflows", async ({ page }) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for execute buttons
		const executeButton = page
			.getByRole("button", { name: /execute|run/i })
			.first();
		const emptyState = page.getByText(
			/no workflows available|no workflows match/i,
		);

		// The heading renders before the workflow query settles. Wait for the
		// loaded list or its empty state instead of sampling during skeletons.
		await expect(executeButton.or(emptyState)).toBeVisible({
			timeout: 10000,
		});
	});

	test("should navigate to execute page when clicking execute", async ({
		page,
	}) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find execute button
		const executeButton = page
			.getByRole("button", { name: /execute|run/i })
			.first();
		const emptyState = page.getByText(
			/no workflows available|no workflows match/i,
		);

		await expect(executeButton.or(emptyState)).toBeVisible({
			timeout: 10000,
		});

		if (await executeButton.isVisible().catch(() => false)) {
			await executeButton.click();

			const isOnExecutePage = page.url().includes("/execute");
			const hasExecutionForm = await page
				.getByRole("button", { name: /run|submit|execute/i })
				.isVisible()
				.catch(() => false);

			expect(isOnExecutePage || hasExecutionForm).toBe(true);
		}
	});
});

test.describe("Workflow Discovery", () => {
	test("should show platform workflows", async ({ page }) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Platform workflows should be visible to admin
		// (specific workflow names depend on what's in the platform directory)
	});

	test("should filter workflows", async ({ page }) => {
		await page.goto("/workflows");

		// Wait for page to load
		await expect(
			page.getByRole("heading", { name: /workflows/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for filter/search input
		const searchInput = page
			.getByPlaceholder(/search|filter/i)
			.or(page.getByRole("searchbox"));

		if (await searchInput.isVisible().catch(() => false)) {
			await searchInput.fill("test");

			// Results should be filtered
			await expect(page.locator("main")).toBeVisible();
		}
	});
});
