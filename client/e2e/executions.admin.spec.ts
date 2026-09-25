/**
 * Execution History Acceptance (Admin)
 *
 * Runs against live services as platform_admin. These tests create real
 * synthetic workflow executions through the public API, then assert browser
 * behavior against persisted execution state.
 */

import { randomUUID } from "node:crypto";
import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";

let workflowUnique = "";
let workflowPath = "";
let workflowFunction = "";
let workflowMarker = "";

function configureWorkflowFixture() {
	workflowUnique = `${Date.now()}_${process.pid}_${randomUUID().replace(/-/g, "_")}`;
	workflowPath = `e2e_execution_acceptance_${workflowUnique}.py`;
	workflowFunction = `e2e_execution_acceptance_${workflowUnique}`;
	workflowMarker = `execution-acceptance-${workflowUnique}`;
}

function workflowContent() {
	return `"""E2E execution acceptance workflow ${workflowUnique}"""
import asyncio
import logging
from bifrost import workflow

logger = logging.getLogger(__name__)

@workflow(
    name="${workflowFunction}",
    description="E2E execution acceptance Playwright fixture",
)
async def ${workflowFunction}(message: str = "default", delay_seconds: float = 0) -> dict:
    logger.info(f"execution acceptance input {message}")
    if delay_seconds:
        await asyncio.sleep(delay_seconds)
    result = {
        "message": message,
        "marker": "${workflowMarker}",
    }
    logger.info(f"execution acceptance output {message}")
    return result
`;
}

type ExecutionStatus =
	| "Pending"
	| "Running"
	| "Success"
	| "Failed"
	| "CompletedWithErrors"
	| "Timeout"
	| "Cancelled";

type WorkflowExecution = {
	execution_id: string;
	workflow_name: string;
	status: ExecutionStatus;
	result?: unknown;
	input_data?: Record<string, unknown>;
	error_message?: string | null;
};

const TERMINAL_STATUSES = new Set<ExecutionStatus>([
	"Success",
	"Failed",
	"CompletedWithErrors",
	"Timeout",
	"Cancelled",
]);

async function registerWorkflow(api: AuthedApi) {
	const writeResp = await api.put("/api/files/editor/content", {
		data: {
			path: workflowPath,
			content: workflowContent(),
			encoding: "utf-8",
		},
	});
	expect(writeResp.ok(), await writeResp.text()).toBe(true);

	const registerResp = await api.post("/api/workflows/register", {
		data: { path: workflowPath, function_name: workflowFunction },
	});
	expect(registerResp.ok(), await registerResp.text()).toBe(true);

	const workflow = (await registerResp.json()) as { id: string };
	expect(workflow.id).toBeTruthy();
	return workflow.id;
}

async function startExecution(
	api: AuthedApi,
	workflowId: string,
	inputData: Record<string, unknown>,
) {
	const response = await api.post("/api/workflows/execute", {
		data: {
			workflow_id: workflowId,
			input_data: inputData,
			form_id: null,
			transient: false,
		},
	});
	expect(response.ok(), await response.text()).toBe(true);

	const body = (await response.json()) as { execution_id: string };
	expect(body.execution_id).toBeTruthy();
	return body.execution_id;
}

async function getExecution(api: AuthedApi, executionId: string) {
	const response = await api.get(`/api/executions/${executionId}`);
	expect(response.ok(), await response.text()).toBe(true);
	return (await response.json()) as WorkflowExecution;
}

async function waitForExecutionStatus(
	api: AuthedApi,
	executionId: string,
	accept: (execution: WorkflowExecution) => boolean,
	timeoutMs = 60_000,
) {
	const startedAt = Date.now();
	let lastExecution: WorkflowExecution | undefined;

	for (;;) {
		lastExecution = await getExecution(api, executionId);
		if (accept(lastExecution)) return lastExecution;
		if (Date.now() - startedAt > timeoutMs) {
			throw new Error(
				`Execution ${executionId} remained ${lastExecution.status} after ${timeoutMs}ms`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
}

async function waitForTerminalSuccess(api: AuthedApi, executionId: string) {
	const execution = await waitForExecutionStatus(
		api,
		executionId,
		(candidate) => TERMINAL_STATUSES.has(candidate.status),
	);
	expect(execution.status).toBe("Success");
	return execution;
}

async function openExecutionDetail(page: Page, executionId: string) {
	await page.goto(`/history/${executionId}`);
	await page.waitForURL(new RegExp(`/history/${executionId}$`));
	await expect(
		page.getByRole("heading", { level: 1, name: workflowFunction }),
	).toBeVisible({ timeout: 10_000 });
}

async function expectNoHorizontalOverflow(page: Page) {
	await expect
		.poll(() =>
			page.evaluate(
				() =>
					document.documentElement.scrollWidth <=
					document.documentElement.clientWidth,
			),
		)
		.toBe(true);
}

test.describe("Execution History", () => {
	let workflowId: string;

	test.beforeAll(async ({ api }) => {
		configureWorkflowFixture();
		workflowId = await registerWorkflow(api);
	});

	test.afterAll(async ({ api }) => {
		if (workflowPath) {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(
						`/api/files/editor?path=${encodeURIComponent(workflowPath)}`,
					)
				).status(),
			);
		}
	});

	test("should display execution history page", async ({ page }) => {
		await page.goto("/history");

		await expect(
			page.getByRole("heading", { name: /history|executions/i }).first(),
		).toBeVisible({ timeout: 10_000 });
	});

	test("should cap wide layouts and fit narrow viewports", async ({
		page,
		api,
	}) => {
		const executionId = await startExecution(api, workflowId, {
			message: "responsive",
		});
		await waitForTerminalSuccess(api, executionId);

		await page.setViewportSize({ width: 2048, height: 900 });
		await page.goto("/history");

		const historyRegion = page.getByRole("region", {
			name: "History",
			exact: true,
		});
		await expect(historyRegion).toBeVisible({ timeout: 10_000 });
		const wideBounds = await historyRegion.boundingBox();
		expect(wideBounds?.width).toBeLessThanOrEqual(1280);
		expect(
			await page
				.locator("main")
				.evaluate(
					(element) => element.scrollHeight <= element.clientHeight,
				),
		).toBe(true);

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
		).toBeVisible({ timeout: 10_000 });
		await expectNoHorizontalOverflow(page);

		await expect(page.getByRole("table")).toHaveCount(0);
		const card = page.getByRole("listitem").filter({
			has: page
				.getByRole("link", { name: workflowFunction, exact: true })
				.and(page.locator(`a[href="/history/${executionId}"]`)),
		});
		await expect(card).toBeVisible();
		await expect(
			card.getByText("Completed", { exact: true }),
		).toBeVisible();
		await expect(
			card.getByRole("button", {
				name: `Preview execution ${workflowFunction}`,
				exact: true,
			}),
		).toBeVisible();
		await expect(card).toContainText("Run by");

		await page.goto("/history?type=agents");
		await expect(
			page.getByText("View agent run history across the fleet"),
		).toBeVisible({ timeout: 10_000 });
		await expectNoHorizontalOverflow(page);
		// Mobile uses natural page scrolling. Bounded main content is the
		// desktop contract; wait for the responsive layout to settle there.
		await page.setViewportSize({ width: 2048, height: 900 });
		await expect
			.poll(() =>
				page
					.locator("main")
					.evaluate(
						(element) =>
							element.scrollHeight <= element.clientHeight,
					),
			)
			.toBe(true);
	});

	test(
		"[EXEC-01 desktop] completed execution survives browser back and shows Result/Input/Logs states",
		{ tag: "@smoke" },
		async ({ page, api }) => {
			const executionId = await startExecution(api, workflowId, {
				message: "exec-01",
			});
			await waitForTerminalSuccess(api, executionId);

			await page.setViewportSize({ width: 1440, height: 500 });
			await page.goto("/history?status=Success");
			const historyRow = page.getByRole("row").filter({
				has: page
					.getByRole("link", { name: workflowFunction, exact: true })
					.and(page.locator(`a[href="/history/${executionId}"]`)),
			});
			await expect(historyRow).toBeVisible({ timeout: 10_000 });
			await historyRow.click();
			await expect(page).toHaveURL(
				new RegExp(
					`/history\\?status=Success&execution=${executionId}$`,
				),
			);
			const historyScrollTop = await page
				.locator("[data-page-scroll]")
				.evaluate((element) => {
					element.scrollTop = 96;
					return element.scrollTop;
				});
			expect(historyScrollTop).toBeGreaterThan(0);
			await page.getByRole("link", { name: "Open execution" }).click();
			await page.waitForURL(new RegExp(`/history/${executionId}$`));
			await expect(
				page
					.locator(".execution-shell")
					.getByText("Completed", { exact: true }),
			).toBeVisible();

			await expect(
				page.getByRole("tab", { name: "Result", exact: true }),
			).toHaveAttribute("aria-selected", "true");
			await expect(
				page.getByRole("tabpanel", { name: "Result", exact: true }),
			).toBeVisible();
			await expect(
				page.getByRole("tabpanel", { name: "Result", exact: true }),
			).toContainText("exec-01");
			await expect(
				page.getByRole("tabpanel", { name: "Result", exact: true }),
			).toContainText(workflowMarker);

			await page.getByRole("tab", { name: "Input", exact: true }).click();
			await expect(
				page.getByRole("tabpanel", { name: "Input", exact: true }),
			).toContainText("exec-01");

			await page.getByRole("tab", { name: "Logs", exact: true }).click();
			await expect(
				page.getByRole("region", { name: "Execution log messages" }),
			).toContainText("execution acceptance output exec-01");

			await page.getByRole("button", { name: "Back to history" }).click();
			await page.waitForURL(
				new RegExp(
					`/history\\?status=Success&execution=${executionId}$`,
				),
			);
			await expect(historyRow).toBeVisible({ timeout: 10_000 });
			await expect(
				historyRow.getByText("Completed", { exact: true }),
			).toBeVisible();
			await expect(
				page.getByRole("complementary", { name: "Execution preview" }),
			).toBeVisible();
			await expect
				.poll(() =>
					page
						.locator("[data-page-scroll]")
						.evaluate((element) => element.scrollTop),
				)
				.toBe(historyScrollTop);
		},
	);

	test("[EXEC-04 desktop] History log row opens drawer with Result/Input/Logs and full-page link for the same execution", async ({
		page,
		api,
		context,
	}) => {
		const executionId = await startExecution(api, workflowId, {
			message: "exec-04",
		});
		await waitForTerminalSuccess(api, executionId);

		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto("/history");
		await expect(
			page.getByRole("region", { name: "History", exact: true }),
		).toBeVisible({ timeout: 10_000 });
		await page
			.getByRole("switch", { name: "Logs view", exact: true })
			.check();

		const logRow = page.getByRole("row").filter({
			hasText: "execution acceptance output exec-04",
		});
		await expect(logRow).toBeVisible({ timeout: 10_000 });
		await logRow.click();

		const drawer = page.getByRole("dialog", { name: "Execution details" });
		await expect(drawer).toBeVisible({ timeout: 10_000 });
		await expect(
			drawer.getByRole("heading", {
				name: workflowFunction,
				exact: true,
			}),
		).toBeVisible({ timeout: 10_000 });
		await expect(
			drawer.getByText("Completed", { exact: true }),
		).toBeVisible();

		await expect(
			drawer.getByRole("tab", { name: "Result", exact: true }),
		).toHaveAttribute("aria-selected", "true");
		await expect(
			drawer.getByRole("tabpanel", { name: "Result", exact: true }),
		).toContainText("exec-04");
		await expect(
			drawer.getByRole("tabpanel", { name: "Result", exact: true }),
		).toContainText(workflowMarker);

		await drawer.getByRole("tab", { name: "Input", exact: true }).click();
		await expect(
			drawer.getByRole("tabpanel", { name: "Input", exact: true }),
		).toContainText("exec-04");

		await drawer.getByRole("tab", { name: "Logs", exact: true }).click();
		await expect(
			drawer.getByRole("region", { name: "Execution log messages" }),
		).toContainText("execution acceptance output exec-04");

		const fullPagePromise = context.waitForEvent("page");
		await drawer
			.getByRole("link", {
				name: "Open execution in new tab",
				exact: true,
			})
			.click();
		const fullPage = await fullPagePromise;
		await fullPage.waitForLoadState("domcontentloaded");
		await expect(fullPage).toHaveURL(
			new RegExp(`/history/${executionId}$`),
		);
		await expect(
			fullPage.getByRole("heading", {
				level: 1,
				name: workflowFunction,
			}),
		).toBeVisible({ timeout: 10_000 });
		await expect(
			fullPage.getByRole("tabpanel", { name: "Result", exact: true }),
		).toContainText("exec-04");
	});

	test(
		"[EXEC-02 desktop] actual rerun creates a new execution with the expected output",
		{ tag: "@smoke" },
		async ({ page, api }) => {
			const originalExecutionId = await startExecution(api, workflowId, {
				message: "exec-02",
			});
			await waitForTerminalSuccess(api, originalExecutionId);

			await page.setViewportSize({ width: 1440, height: 900 });
			await openExecutionDetail(page, originalExecutionId);
			await page
				.getByRole("button", { name: "Rerun", exact: true })
				.click();
			await expect(
				page.getByRole("alertdialog", { name: "Rerun Workflow?" }),
			).toBeVisible();
			await page
				.getByRole("button", { name: "Yes, rerun workflow" })
				.click();

			await expect(page).toHaveURL(
				(url) =>
					/\/history\/[0-9a-f-]{36}$/.test(url.pathname) &&
					!url.pathname.endsWith(originalExecutionId),
				{ timeout: 10_000 },
			);
			const rerunExecutionId = page
				.url()
				.match(/\/history\/([0-9a-f-]{36})$/)?.[1];
			expect(rerunExecutionId).toBeTruthy();
			expect(rerunExecutionId).not.toBe(originalExecutionId);

			await waitForTerminalSuccess(api, rerunExecutionId!);
			await page.reload();
			await openExecutionDetail(page, rerunExecutionId!);
			await expect(
				page.getByText("Completed", { exact: true }),
			).toBeVisible();
			await expect(
				page.getByRole("tabpanel", { name: "Result", exact: true }),
			).toContainText("exec-02");
			await expect(
				page.getByRole("tabpanel", { name: "Result", exact: true }),
			).toContainText(workflowMarker);
		},
	);

	test("[EXEC-03 mobile] cancellation of a running execution reaches a cancelled state", async ({
		page,
		api,
	}) => {
		test.setTimeout(90_000);

		const executionId = await startExecution(api, workflowId, {
			message: "exec-03",
			delay_seconds: 30,
		});
		await waitForExecutionStatus(
			api,
			executionId,
			(execution) =>
				execution.status === "Pending" ||
				execution.status === "Running",
			20_000,
		);

		await page.setViewportSize({ width: 390, height: 844 });
		await openExecutionDetail(page, executionId);
		await page
			.getByRole("button", { name: "Cancel", exact: true })
			.waitFor();
		await page.screenshot({
			path: test.info().outputPath("running-mobile.png"),
		});
		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.screenshot({
			path: test.info().outputPath("running-desktop.png"),
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.getByRole("button", { name: "Cancel", exact: true }).click();
		await expect(
			page.getByRole("alertdialog", { name: "Cancel Execution?" }),
		).toBeVisible();
		await page
			.getByRole("button", { name: "Yes, cancel execution" })
			.click();

		await waitForExecutionStatus(
			api,
			executionId,
			(execution) => execution.status === "Cancelled",
			60_000,
		);
		await page.reload();
		await openExecutionDetail(page, executionId);
		await expect(
			page.getByText("Cancelled", { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("This run was cancelled", { exact: true }),
		).toBeVisible();
		await expectNoHorizontalOverflow(page);
	});
});
