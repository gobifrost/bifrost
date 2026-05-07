/**
 * Execution History Tests (Admin)
 *
 * Tests execution history viewing and management from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_executions.py
 */

import {
	test,
	expect,
	type AuthedApi,
} from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

interface CompletedExecution {
	executionId: string;
	workflowName: string;
}

let completedExecution: CompletedExecution;

function rowForWorkflow(page: Page, workflowName: string) {
	return page
		.locator("table tbody tr")
		.filter({ hasText: workflowName });
}

async function createCompletedExecution(
	api: AuthedApi,
	workflowName: string,
): Promise<CompletedExecution> {
	const code = `
import logging

logger = logging.getLogger(__name__)
logger.info("history page e2e log line")

result = {"ok": True, "workflow": "${workflowName}"}
`.trim();

	const response = await api.post("/api/workflows/execute", {
		data: {
			workflow_id: null,
			input_data: {},
			form_id: null,
			transient: false,
			code,
			script_name: workflowName,
		},
	});
	expect(response.ok(), await response.text()).toBe(true);
	const body = await response.json();
	const executionId = body.execution_id as string;
	expect(executionId).toBeTruthy();

	await expect
		.poll(
			async () => {
				const details = await api.get(`/api/executions/${executionId}`);
				if (!details.ok()) {
					return `HTTP ${details.status()}`;
				}
				const execution = await details.json();
				return execution.status as string;
			},
			{
				timeout: 60_000,
				intervals: [1_000],
				message: "execution did not complete before history assertions",
			},
		)
		.toBe("Success");

	return { executionId, workflowName };
}

async function openExecutionDrawer(page: Page) {
	await page.goto("/history");

	const row = rowForWorkflow(page, completedExecution.workflowName);
	await expect(row).toBeVisible({ timeout: 15_000 });
	await row.click();

	const drawer = page.getByRole("dialog", { name: /execution details/i });
	await expect(drawer).toBeVisible({ timeout: 10_000 });
	return drawer;
}

test.beforeAll(async ({ api }, testInfo) => {
	testInfo.setTimeout(90_000);
	const suffix = `${Date.now()}_${testInfo.workerIndex}_${Math.floor(
		Math.random() * 10000,
	)}`;
	const workflowName = `e2e_history_${suffix}`;
	completedExecution = await createCompletedExecution(api, workflowName);
});

test.describe("Execution History", () => {
	test("should display execution history page", async ({ page }) => {
		await page.goto("/history");

		// Should see history heading
		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });
	});

	test("should list executions", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		await expect(
			rowForWorkflow(page, completedExecution.workflowName),
		).toBeVisible({ timeout: 15_000 });
	});

	test("should show execution status badges", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		await expect(
			rowForWorkflow(page, completedExecution.workflowName).getByText(
				"Completed",
				{ exact: true },
			),
		).toBeVisible({ timeout: 15_000 });
	});
});

test.describe("Execution Details", () => {
	test("should open execution details from history", async ({ page }) => {
	const drawer = await openExecutionDrawer(page);

	await expect(
		drawer.getByRole("heading", {
			name: completedExecution.workflowName,
		}),
	).toBeVisible({ timeout: 10_000 });
		await expect(
			drawer.getByText("Completed", { exact: true }),
		).toBeVisible();
	});

	test("should show execution output/results", async ({ page }) => {
		const drawer = await openExecutionDrawer(page);

		await expect(
			drawer.getByText("Workflow execution result"),
		).toBeVisible({ timeout: 10_000 });
		await expect(drawer.getByText("Ok", { exact: true })).toBeVisible();
	});

	test("should show execution logs", async ({ page }) => {
		const drawer = await openExecutionDrawer(page);

		await expect(
			drawer.getByText("history page e2e log line"),
		).toBeVisible({ timeout: 10_000 });
	});
});

test.describe("Execution Filtering", () => {
	test("should filter by status", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10000 });

		await page.getByRole("tab", { name: "Completed" }).click();
		await expect(
			rowForWorkflow(page, completedExecution.workflowName),
		).toBeVisible({ timeout: 10_000 });
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
			await searchInput.fill(completedExecution.workflowName);

			await expect(
				rowForWorkflow(page, completedExecution.workflowName),
			).toBeVisible({ timeout: 10_000 });
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

		const drawer = await openExecutionDrawer(page);
		await expect(drawer.getByTitle("Rerun")).toBeVisible({
			timeout: 10_000,
		});
	});
});
