/**
 * Scheduled Executions Happy Paths (Admin)
 *
 * End-to-end coverage for the deferred-executions feature surfaced on the
 * History page:
 *
 * 1. Schedule → Success: a row scheduled with a short delay is listed as
 *    `Scheduled`, the promoter ticks (every 60s), the worker runs it, and the
 *    badge flips to `Completed` (the label the UI uses for Success) after a
 *    manual refresh.
 * 2. Cancel from row menu: a long-delayed scheduled row can be cancelled via
 *    the row's icon button → AlertDialog → confirm, and the badge flips to
 *    `Cancelled` (optimistically, then server-backed).
 *
 * Fixture: registers a trivial workflow via /api/workflows/register. The
 * inline-code path is blocked for scheduled executions server-side (contract
 * rejects `code + scheduled_at`), so we need a real workflow registration.
 *
 * Runs under the `chromium` project (default), which is authenticated as
 * platform_admin.
 */

import { test, expect, type Page } from "./fixtures/api-fixture";

// Unique suffix keeps workflow names distinct across parallel worktrees and
// re-runs, so stale `Scheduled` rows from previous runs can't collide with a
// workflow_name text filter. Underscore-only — this is embedded in a Python
// identifier (function name) and the file path.
const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
const WORKFLOW_PATH = `e2e_scheduled_ui_${UNIQUE}.py`;
const WORKFLOW_FUNCTION = `e2e_scheduled_ui_${UNIQUE}`;

// Tiny workflow that returns a fixed dict. Trivially fast on the worker once
// the promoter flips the row to Pending.
const WORKFLOW_CONTENT = `"""E2E scheduled UI workflow ${UNIQUE}"""
from bifrost import workflow

@workflow(
    name="${WORKFLOW_FUNCTION}",
    description="E2E scheduled-execution Playwright fixture",
)
async def ${WORKFLOW_FUNCTION}() -> dict:
    return {"ok": True}
`;

/**
 * Locate the table row for a given workflow name. DataTableRow does NOT
 * render the `href` prop as an <a> tag (it only uses it for cmd/ctrl-click
 * open-in-new-tab), so there's no anchor to match on. The workflow name is
 * rendered as the row's first mono-font cell content and is unique per test
 * run (suffixed with `UNIQUE`), so text is the reliable selector.
 */
function rowForWorkflow(
	page: Page,
	workflowName: string,
	status?: "Scheduled" | "Cancelled" | "Completed",
) {
	let row = page.locator("table tbody tr").filter({ hasText: workflowName });
	if (status) {
		row = row.filter({
			has: page.getByText(status, { exact: true }),
		});
	}
	return row;
}

/**
 * Click the toolbar refresh button (icon button wrapping <RefreshCw/>) to
 * force a server refetch. The list page does NOT auto-poll, so tests that
 * wait for server-side state changes must trigger refetches themselves.
 */
async function clickRefresh(page: Page) {
	// The refresh button is the only button in the page toolbar whose sole
	// child is a `.lucide-refresh-cw` icon. Target it via that class to avoid
	// collision with other ghost/icon buttons on the page.
	await page.locator("button:has(svg.lucide-refresh-cw)").first().click();
}

test.describe("Scheduled executions", () => {
	let workflowId: string;

	test.beforeAll(async ({ api }) => {
		// Write the workflow file then register the decorated function.
		// Mirrors `write_and_register` in api/tests/e2e/conftest.py.
		const writeResp = await api.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				content: WORKFLOW_CONTENT,
				encoding: "utf-8",
			},
		});
		expect(writeResp.ok(), await writeResp.text()).toBe(true);

		const registerResp = await api.post("/api/workflows/register", {
			data: { path: WORKFLOW_PATH, function_name: WORKFLOW_FUNCTION },
		});
		expect(registerResp.ok(), await registerResp.text()).toBe(true);
		const workflow = await registerResp.json();
		workflowId = workflow.id;
		expect(workflowId).toBeTruthy();
	});

	test.afterAll(async ({ api }) => {
		// Remove the workflow file so the filesystem / workflow registry don't
		// accumulate cruft across re-runs. 404 is fine — the file may already
		// be gone in some edge cases.
		await api.delete(
			`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
		);
	});

	test("cancel a scheduled run from the row menu", async ({ page, api }) => {
		// Long delay so the promoter (60s interval) can't fire during the
		// test. This path does NOT require the worker, so it is cheap and
		// reliable.
		const scheduleResp = await api.post("/api/workflows/execute", {
			data: {
				workflow_id: workflowId,
				input_data: {},
				delay_seconds: 600,
			},
		});
		expect(scheduleResp.ok(), await scheduleResp.text()).toBe(true);
		const body = await scheduleResp.json();
		expect(body.status).toBe("Scheduled");

		await page.goto("/history");

		// Filter to Scheduled so our row is visible without scrolling / paging
		// past recent terminal rows.
		await page.getByRole("tab", { name: "Scheduled" }).click();

		const row = rowForWorkflow(page, WORKFLOW_FUNCTION, "Scheduled");
		await expect(row).toBeVisible({ timeout: 15_000 });
		await expect(row.getByText("Scheduled", { exact: true })).toBeVisible();

		// Click the row's Cancel icon button (title attribute set in
		// ExecutionHistory.tsx for Scheduled rows only).
		await row
			.getByRole("button", { name: "Cancel scheduled execution" })
			.click();

		// AlertDialog confirms the destructive action.
		await expect(
			page.getByRole("alertdialog", { name: "Cancel scheduled run?" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Confirm cancel" }).click();

		// Optimistic update flips the badge to `Cancelled` immediately;
		// post-refetch it stays Cancelled because the server row is also
		// Cancelled. Switch to All so the row is visible regardless of which
		// tab the row lands on after the status change.
		await page.getByRole("tab", { name: "All" }).click();
		const rowAfter = rowForWorkflow(page, WORKFLOW_FUNCTION, "Cancelled");
		await expect(
			rowAfter.getByText("Cancelled", { exact: true }),
		).toBeVisible({ timeout: 10_000 });
	});

	test("schedule with short delay and watch the badge flip to Completed", async ({
		page,
		api,
	}) => {
		// The promoter runs every 60s. A schedule with delay_seconds=3 means
		// the row becomes due almost immediately but must wait for the next
		// promoter tick. Worst case we wait ~60s for the tick + ~5s for the
		// worker. Give generous headroom for CI.
		test.setTimeout(180_000);

		const scheduleResp = await api.post("/api/workflows/execute", {
			data: {
				workflow_id: workflowId,
				input_data: {},
				delay_seconds: 3,
			},
		});
		expect(scheduleResp.ok(), await scheduleResp.text()).toBe(true);
		const body = await scheduleResp.json();
		expect(body.status).toBe("Scheduled");

		await page.goto("/history");
		await page.getByRole("tab", { name: "Scheduled" }).click();

		const row = rowForWorkflow(page, WORKFLOW_FUNCTION, "Scheduled");
		await expect(row).toBeVisible({ timeout: 15_000 });
		await expect(row.getByText("Scheduled", { exact: true })).toBeVisible();

		// Poll: hit refresh, check for `Completed` on ANY tab. Once the
		// promoter fires, the row leaves the Scheduled tab (status becomes
		// Pending → Running → Success/Completed), so we switch to All to
		// keep the row locator stable.
		await page.getByRole("tab", { name: "All" }).click();
		const rowOnAll = rowForWorkflow(page, WORKFLOW_FUNCTION);

		// Target the status badge specifically to avoid matching the
		// "Completed" tab trigger or the "Completed At" column header.
		await expect
			.poll(
				async () => {
					await clickRefresh(page);
					// Small settle delay so the badge reflects the refetched
					// row. 1s is enough for $api.useQuery to paint a new row.
					await page.waitForTimeout(1000);
					const badge = rowOnAll.getByText("Completed", {
						exact: true,
					});
					return (await badge.count()) > 0;
				},
				{
					timeout: 120_000,
					intervals: [5_000],
					message:
						"scheduled row never promoted to Completed within 120s",
				},
			)
			.toBe(true);
	});

	test("schedule a run from the workflow execute page", async ({ page }) => {
		// Navigate to the execute page. handleExecute in Workflows.tsx constructs
		// the URL as `/workflows/${workflowName}/execute` — WORKFLOW_FUNCTION is
		// underscore-only so no encoding needed.
		await page.goto(`/workflows/${WORKFLOW_FUNCTION}/execute`);

		// The workflow has no required params, so we go straight to scheduling.
		// ScheduleControls renders a checkbox with aria-label="Schedule for later".
		await page.getByLabel("Schedule for later").check();

		// Click the "In 15 min" quick-pick button.
		await page.getByRole("button", { name: "In 15 min" }).click();

		// Submit the outer form via the "Execute Workflow" button.
		await page.getByRole("button", { name: "Execute Workflow" }).click();

		// ExecuteWorkflow navigates to /history and toasts "Scheduled for ...".
		await expect(page).toHaveURL(/\/history/, { timeout: 10_000 });
		await expect(page.getByText(/scheduled for/i).first()).toBeVisible({
			timeout: 5_000,
		});

		// Filter to Scheduled so our row is near the top.
		await page.getByRole("tab", { name: "Scheduled" }).click();
		const row = rowForWorkflow(page, WORKFLOW_FUNCTION, "Scheduled");
		await expect(row).toBeVisible({ timeout: 10_000 });
		await expect(row.getByText("Scheduled", { exact: true })).toBeVisible();
	});
});
