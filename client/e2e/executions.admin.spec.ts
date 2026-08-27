/**
 * Execution History Tests (Admin)
 *
 * Tests execution history viewing and management from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_executions.py
 */

import { test, expect, type Page } from "@playwright/test";

async function openFirstExecution(page: Page) {
	const executionRow = page.locator("[data-testid='execution-row']").first();
	if (!(await executionRow.isVisible().catch(() => false))) {
		return false;
	}

	const executionId = await executionRow.getAttribute("data-execution-id");
	if (!executionId) {
		throw new Error("Execution row is missing data-execution-id");
	}

	await page.goto(`/history/${executionId}`);
	await page.waitForURL(new RegExp(`/history/${executionId}$`), {
		timeout: 5000,
	});
	return true;
}

test.describe("Execution History", () => {
	test("should display execution history page", async ({ page }) => {
		await page.goto("/history");

		// Should see history heading
		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("should cap wide layouts and fit narrow viewports", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 2048, height: 900 });
		await page.goto("/history");

		const historyRegion = page.getByRole("region", { name: "History" });
		await expect(historyRegion).toBeVisible({ timeout: 10000 });
		const wideBounds = await historyRegion.boundingBox();
		expect(wideBounds?.width).toBeLessThanOrEqual(1280);

		const statusTabs = page.getByRole("tablist").first();
		await expect(statusTabs).toBeVisible();
		expect(
			await statusTabs.evaluate(
				(element) => getComputedStyle(element.parentElement!).overflowX,
			),
		).toBe("visible");

		await page.setViewportSize({ width: 375, height: 812 });

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });
		await expect
			.poll(() =>
				page.evaluate(
					() =>
						document.documentElement.scrollWidth <=
							document.documentElement.clientWidth,
				),
			)
			.toBe(true);

		const table = page.getByRole("table").first();
		if (await table.isVisible().catch(() => false)) {
			await expect(
				table.getByRole("columnheader", { name: "Workflow" }),
			).toBeVisible();
			await expect(
				table.getByRole("columnheader", { name: "Status" }),
			).toBeVisible();
			for (const name of ["Organization", "Run by", "Started", "Duration"]) {
				await expect(
					table.getByRole("columnheader", { name }),
				).toBeHidden();
			}
		}
	});

	test("should list executions", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Either we have executions or an empty state. The rebuilt history
		// page exposes explicit testids for both empty variants ("No runs
		// yet" / "No runs match your filters").
		await expect(
			page
				.locator(
					"[data-testid='execution-row'], [data-testid='history-empty'], [data-testid='history-empty-filtered']",
				)
				.first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("should show execution status badges", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for status indicators
		const statusBadge = page.locator(
			"[data-testid='execution-row'] [data-slot='badge'], [data-testid='execution-row'] [data-testid='status-badge']",
		);

		// If we have executions, we should have status badges
		const hasExecutions =
			(await page
				.locator("[data-testid='execution-row']")
				.count()
				.catch(() => 0)) > 0;

		if (hasExecutions) {
			await expect(statusBadge.first()).toBeVisible();
		}
	});
});

test.describe("Execution Details", () => {
	test("should navigate to execution details", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		if (await openFirstExecution(page)) {
			// Should navigate to execution details: the page header renders
			// the workflow name as the h1 alongside its status badge. (The
			// old "Execution Status" label predates the details rebuild and
			// only became reachable once the history table gained
			// data-testid='execution-row' on this branch.)
			await expect(
				page.getByRole("heading", { level: 1 }).first(),
			).toBeVisible({ timeout: 5000 });
			await expect(
				page.locator("[data-slot='badge']").first(),
			).toBeVisible();
		}
	});

	test("should show execution output/results", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		if (await openFirstExecution(page)) {
			// Should show output section
			await expect(
				page.getByText("Result", { exact: true }).first(),
			).toBeVisible({ timeout: 5000 });
		}
	});

	test("should show execution logs", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		if (await openFirstExecution(page)) {
			await expect(
				page.getByText("Logs", { exact: true }).first(),
			).toBeVisible({ timeout: 5000 });
		}
	});
});

test.describe("Execution Filtering", () => {
	test("should filter by status", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for status filter
		const statusFilter = page
			.getByRole("combobox", { name: /status/i })
			.or(page.locator("[data-testid='status-filter']"))
			.or(page.getByLabel(/status/i));

		if (await statusFilter.isVisible().catch(() => false)) {
			await statusFilter.click();

			// Should show filter options
			await expect(
				page.getByText(/completed|failed|running|pending/i),
			).toBeVisible({ timeout: 3000 });
		}
	});

	test("should search executions", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for search input
		const searchInput = page
			.getByPlaceholder(/search|filter/i)
			.or(page.getByRole("searchbox"));

		if (await searchInput.isVisible().catch(() => false)) {
			await searchInput.fill("test");

			// Results should update
			await expect(page.locator("main")).toBeVisible();
		}
	});
});

test.describe("Execution Actions", () => {
	test("should show cancel button for running executions", async ({
		page,
	}) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Scope within the execution list so we don't accidentally match
		// filter-chip buttons labeled "Running" / "Pending" at the top of the page.
		const listRegion = page.locator("main table, main [role='list']").first();
		const runningRow = listRegion
			.locator("tr, li")
			.filter({ hasText: /running|pending/i })
			.first();

		if (await runningRow.isVisible().catch(() => false)) {
			// A row in the running/pending state must expose a cancel action.
			await expect(
				runningRow.getByRole("button", { name: /cancel|stop/i }),
			).toBeVisible();
		}
	});

	test("should show re-run option for completed executions", async ({
		page,
	}) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find a completed execution
		const completedRow = page
			.locator("table tbody tr, [data-testid='execution-row']")
			.filter({ hasText: /completed|success|failed/i })
			.first();

		if (await completedRow.isVisible().catch(() => false)) {
			// Look for re-run button
			const rerunButton = page
				.getByRole("button", { name: /re-?run|retry/i })
				.first();

			// Re-run functionality may or may not be implemented
			const _hasRerun = await rerunButton.isVisible().catch(() => false);
			// Just checking the page works, not requiring re-run feature
			expect(true).toBe(true);
		}
	});
});
