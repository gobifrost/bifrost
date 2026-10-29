import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

type DashboardMetrics = {
	workflow_count: number;
	form_count: number;
	roi_24h?: {
		total_time_saved: number;
		total_value: number;
		value_unit: string;
	} | null;
};

type ExecutionTimeSeries = {
	success_count: number;
	failed_count: number;
	total_count: number;
	success_rate: number | null;
};

type ApplicationsList = { total?: number } | unknown[];
type CreatedRecord = { id: string };

type WorkflowRecord = {
	id: string;
	name: string;
	display_name?: string | null;
};

const unique = `${Date.now()}_${randomUUID().replaceAll("-", "_")}`;
const workflowFunction = `e2e_dashboard_acceptance_${unique}`;
const workflowPath = `${workflowFunction}.py`;
const workflowName = `Dashboard Acceptance ${unique}`;
const cleanupPaths = [workflowPath];

function workflowContent() {
	return `from bifrost import workflow\n\n@workflow(name="${workflowName}", description="Dashboard acceptance fixture")\nasync def ${workflowFunction}() -> dict:\n    return {"ok": True, "marker": "${unique}"}\n`;
}

async function expectOk(
	response: Pick<
		Awaited<ReturnType<AuthedApi["get"]>>,
		"ok" | "text" | "status"
	>,
	label: string,
) {
	expect(
		response.ok(),
		`${label}: ${response.status()} ${await response.text()}`,
	).toBe(true);
}

async function registerWorkflow(api: AuthedApi) {
	const write = await api.put("/api/files/editor/content", {
		data: {
			path: workflowPath,
			encoding: "utf-8",
			content: workflowContent(),
		},
	});
	await expectOk(write, "write dashboard workflow fixture");

	const register = await api.post("/api/workflows/register", {
		data: { path: workflowPath, function_name: workflowFunction },
	});
	await expectOk(register, "register dashboard workflow fixture");
	const workflow = (await register.json()) as CreatedRecord;
	expect(workflow.id).toBeTruthy();
	return workflow.id;
}

async function readWorkflow(api: AuthedApi, workflowId: string) {
	const response = await api.get("/api/workflows");
	await expectOk(response, "read workflows");
	const workflows = (await response.json()) as WorkflowRecord[];
	const workflow = workflows.find((item) => item.id === workflowId);
	expect(
		workflow,
		"registered workflow appears in workflow inventory",
	).toBeDefined();
	return workflow!;
}

async function deleteWorkflowSource(api: AuthedApi) {
	for (const path of cleanupPaths) {
		const response = await api.delete(
			`/api/files/editor?path=${encodeURIComponent(path)}`,
		);
		expect(
			[200, 204, 404],
			`delete workflow source ${path}: ${response.status()}`,
		).toContain(response.status());
	}
}

function responsePath(responseUrl: string) {
	return new URL(responseUrl).pathname;
}

function isMetricsResponse(responseUrl: string) {
	return responsePath(responseUrl) === "/api/metrics";
}

function isTimeseriesResponse(responseUrl: string) {
	return responsePath(responseUrl) === "/api/metrics/executions/timeseries";
}

function isAgentsResponse(responseUrl: string) {
	return responsePath(responseUrl) === "/api/agents";
}

function isApplicationsResponse(responseUrl: string) {
	return responsePath(responseUrl) === "/api/applications";
}

function formatNumber(value: number) {
	return value.toLocaleString("en-US");
}

function formatTimeSaved(minutes: number) {
	const hours = Math.floor(minutes / 60);
	const mins = minutes % 60;
	if (hours > 0) return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
	return `${mins}m`;
}

function formatValue(value: number) {
	return value.toLocaleString("en-US", {
		minimumFractionDigits: 0,
		maximumFractionDigits: 2,
	});
}

function appCount(applications: ApplicationsList) {
	return Array.isArray(applications)
		? applications.length
		: (applications.total ?? 0);
}

function escapeRegExp(value: string) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cardName(label: string, value: number | string) {
	return new RegExp(`${label}\\s*${escapeRegExp(String(value))}`);
}

let workflowId: string;
let seededWorkflow: WorkflowRecord;

test.beforeAll(async ({ api }) => {
	workflowId = await registerWorkflow(api);
	seededWorkflow = await readWorkflow(api, workflowId);
	// A populated chart exercises Decimal's constructor; the empty state does not.
	const execute = await api.post("/api/workflows/execute", {
		data: { workflow_id: workflowId, input_data: {} },
	});
	await expectOk(execute, "execute dashboard workflow fixture");
	const { execution_id } = (await execute.json()) as { execution_id: string };
	await expect
		.poll(
			async () => {
				const response = await api.get(
					`/api/executions/${execution_id}`,
				);
				await expectOk(response, "read dashboard execution fixture");
				return ((await response.json()) as { status: string }).status;
			},
			{ timeout: 30_000 },
		)
		.toBe("Success");
});

test.afterAll(async ({ api }) => {
	await deleteWorkflowSource(api);
});

test("DASHBOARD-PRIMARY-01 renders API-backed metrics, refreshes, and drills into workflows", async ({
	page,
}) => {
	const pageErrors: string[] = [];
	page.on("pageerror", (error) => pageErrors.push(error.message));
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.goto("/workflows");
	await page.getByRole("button", { name: "Shell (Cmd+/)" }).click();
	const editor = page.getByRole("dialog", { name: "Code editor" });
	await editor.getByRole("button", { name: "Files", exact: true }).click();
	await editor
		.getByRole("button", {
			name: new RegExp(`^${escapeRegExp(workflowPath)}(?:\\s|$)`),
		})
		.click();
	await expect(
		editor.getByRole("textbox", { name: "Editor content", exact: true }),
	).toBeVisible();
	await page.getByRole("button", { name: "Close editor" }).click();
	await page.getByRole("link", { name: "Home", exact: true }).click();

	const metricsPromise = page.waitForResponse(
		(response) => isMetricsResponse(response.url()) && response.ok(),
	);
	const timeseriesPromise = page.waitForResponse(
		(response) => isTimeseriesResponse(response.url()) && response.ok(),
	);
	const agentsPromise = page.waitForResponse(
		(response) => isAgentsResponse(response.url()) && response.ok(),
	);
	const applicationsPromise = page.waitForResponse(
		(response) => isApplicationsResponse(response.url()) && response.ok(),
	);

	// Stay in the same document: a hard navigation would clear Monaco's AMD global.
	await page.getByRole("link", { name: "Dashboard", exact: true }).click();
	await expect(page.getByRole("application")).toBeVisible();
	expect(await page.evaluate(() => "define" in window)).toBe(false);
	const [
		metricsResponse,
		timeseriesResponse,
		agentsResponse,
		applicationsResponse,
	] = await Promise.all([
		metricsPromise,
		timeseriesPromise,
		agentsPromise,
		applicationsPromise,
	]);

	const metrics = (await metricsResponse.json()) as DashboardMetrics;
	const timeseries = (await timeseriesResponse.json()) as ExecutionTimeSeries;
	const agents = (await agentsResponse.json()) as unknown[];
	const applications =
		(await applicationsResponse.json()) as ApplicationsList;

	await expect(
		page.getByRole("heading", { name: "Dashboard", exact: true }),
	).toBeVisible();
	await expect(
		page.getByText("Platform overview and metrics", { exact: true }),
	).toBeVisible();

	const workflowsMetricLink = page.getByRole("link", {
		name: cardName("Workflows", formatNumber(metrics.workflow_count)),
	});
	await expect(workflowsMetricLink).toBeVisible();
	await expect(
		page.getByRole("link", {
			name: cardName("Forms", formatNumber(metrics.form_count)),
		}),
	).toBeVisible();
	await expect(
		page.getByRole("link", {
			name: cardName("Agents", formatNumber(agents.length)),
		}),
	).toBeVisible();
	await expect(
		page.getByRole("link", {
			name: cardName("Apps", formatNumber(appCount(applications))),
		}),
	).toBeVisible();

	const expectedSuccessRate =
		timeseries.success_rate === null
			? "—"
			: `${timeseries.success_rate.toFixed(1)}%`;
	await expect(
		page.getByRole("link", {
			name: cardName("Success Rate", expectedSuccessRate),
		}),
	).toBeVisible();
	await expect(
		page.getByRole("link", {
			name: cardName("Executions", formatNumber(timeseries.total_count)),
		}),
	).toBeVisible();
	await expect(
		page.getByText(
			new RegExp(
				`Last 7 days · ${escapeRegExp(formatNumber(timeseries.total_count))} ${timeseries.total_count === 1 ? "run" : "runs"}`,
			),
		),
	).toBeVisible();

	const roi = metrics.roi_24h;
	await expect(
		page.getByRole("link", { name: /Value \(24h\)/ }),
	).toContainText(formatTimeSaved(roi?.total_time_saved ?? 0));
	await expect(
		page.getByRole("link", { name: /Value \(24h\)/ }),
	).toContainText(formatValue(roi?.total_value ?? 0));
	await expect(
		page.getByRole("link", { name: /Value \(24h\)/ }),
	).toContainText(roi?.value_unit ?? "USD");

	const refreshMetricsPromise = page.waitForResponse(
		(response) => isMetricsResponse(response.url()) && response.ok(),
	);
	const refreshTimeseriesPromise = page.waitForResponse(
		(response) => isTimeseriesResponse(response.url()) && response.ok(),
	);
	await page.getByRole("button", { name: "Refresh dashboard" }).click();
	await Promise.all([refreshMetricsPromise, refreshTimeseriesPromise]);
	await expect(
		page.getByRole("heading", { name: "Dashboard", exact: true }),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Refresh dashboard" }),
	).toBeEnabled();

	await workflowsMetricLink.click();
	await expect(page).toHaveURL(/\/workflows$/);
	await expect(
		page.getByRole("heading", { name: "Workflows", exact: true }),
	).toBeVisible();
	await page
		.getByPlaceholder("Search by name, description, or category...")
		.fill(seededWorkflow.display_name ?? seededWorkflow.name);
	await expect(
		page.getByRole("button", {
			name: `${seededWorkflow.name} actions`,
			exact: true,
		}),
	).toBeVisible();
	expect(pageErrors).toEqual([]);
});
