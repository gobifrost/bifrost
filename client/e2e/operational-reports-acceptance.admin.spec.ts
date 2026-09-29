import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

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
};

type ROIByWorkflow = {
	workflows: Array<{
		workflow_name: string;
		execution_count: number;
		success_count: number;
		time_saved_per_execution: number;
		value_per_execution: number;
		total_time_saved: number;
		total_value: number;
	}>;
};

type UsageReport = {
	by_conversation: Array<{
		conversation_id: string;
		conversation_title: string | null;
		message_count: number;
		input_tokens: number;
		output_tokens: number;
	}>;
};

type CreatedRecord = { id: string };

const UNIQUE = `${Date.now()}_${process.pid}_${randomUUID().replace(/-/g, "_")}`;
const WORKFLOW_FUNCTION = `e2e_operational_reports_${UNIQUE}`;
const WORKFLOW_PATH = `e2e_operational_reports_${UNIQUE}.py`;
const WORKFLOW_NAME = `Operational Reports Fixture ${UNIQUE}`;
const WORKFLOW_TIME_SAVED = 45;
const WORKFLOW_VALUE = 123;
const CONNECTION_NAME = `Operational Reports Fixture ${UNIQUE}`;
const PROFILE_NAME = `Operational Reports Profile ${UNIQUE}`;
const AGENT_NAME = `Operational Reports Agent ${UNIQUE}`;
const CONVERSATION_TITLE = `Operational Reports Usage ${UNIQUE}`;
const USER_PROMPT = `Record deterministic usage ${UNIQUE}`;

const TERMINAL_STATUSES = new Set<ExecutionStatus>([
	"Success",
	"Failed",
	"CompletedWithErrors",
	"Timeout",
	"Cancelled",
]);

function workflowContent() {
	return `"""Operational reports acceptance workflow fixture."""
from bifrost import workflow

@workflow(
    name="${WORKFLOW_NAME}",
    description="Operational reports acceptance fixture",
)
async def ${WORKFLOW_FUNCTION}() -> dict:
    return {"ok": True, "marker": "${UNIQUE}"}
`;
}

function reportDateRange() {
	const end = new Date().toISOString().slice(0, 10);
	const startDate = new Date();
	startDate.setDate(startDate.getDate() - 30);
	return { start: startDate.toISOString().slice(0, 10), end };
}

async function expectOk(
	response: Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">,
) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectDeleted(response: Awaited<ReturnType<AuthedApi["delete"]>>) {
	expect([200, 204, 404]).toContain(response.status());
}

async function registerWorkflow(api: AuthedApi) {
	const writeResp = await api.put("/api/files/editor/content", {
		data: {
			path: WORKFLOW_PATH,
			content: workflowContent(),
			encoding: "utf-8",
		},
	});
	await expectOk(writeResp);

	const registerResp = await api.post("/api/workflows/register", {
		data: { path: WORKFLOW_PATH, function_name: WORKFLOW_FUNCTION },
	});
	await expectOk(registerResp);
	const workflow = (await registerResp.json()) as CreatedRecord;

	const updateResp = await api.patch(`/api/workflows/${workflow.id}`, {
		data: {
			name: WORKFLOW_NAME,
			display_name: WORKFLOW_NAME,
			access_level: "authenticated",
			execution_mode: "sync",
			time_saved: WORKFLOW_TIME_SAVED,
			value: WORKFLOW_VALUE,
		},
	});
	await expectOk(updateResp);

	return workflow.id;
}

async function executeWorkflow(api: AuthedApi, workflowId: string) {
	const response = await api.post("/api/workflows/execute", {
		data: {
			workflow_id: workflowId,
			input_data: {},
			form_id: null,
			transient: false,
			sync: true,
		},
	});
	await expectOk(response);
	const body = (await response.json()) as { execution_id: string };
	expect(body.execution_id).toBeTruthy();
	return body.execution_id;
}

async function getExecution(api: AuthedApi, executionId: string) {
	const response = await api.get(`/api/executions/${executionId}`);
	await expectOk(response);
	return (await response.json()) as WorkflowExecution;
}

async function waitForTerminalSuccess(api: AuthedApi, executionId: string) {
	const startedAt = Date.now();
	let lastExecution: WorkflowExecution | undefined;
	for (;;) {
		lastExecution = await getExecution(api, executionId);
		if (TERMINAL_STATUSES.has(lastExecution.status)) break;
		if (Date.now() - startedAt > 30_000) {
			throw new Error(
				`Execution ${executionId} remained ${lastExecution.status} after 30000ms`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
	expect(lastExecution.status).toBe("Success");
	return lastExecution;
}

async function createFixtureProfile(api: AuthedApi) {
	let connectionId: string | null = null;
	try {
		const connectionResponse = await api.post("/api/admin/ai/connections", {
			data: {
				name: CONNECTION_NAME,
				provider: "openai_compatible",
				api_key: "fixture-key",
				endpoint: "http://scheduler-fixtures:8080/v1",
			},
		});
		await expectOk(connectionResponse);
		const connection = (await connectionResponse.json()) as CreatedRecord;
		connectionId = connection.id;

		const profileResponse = await api.post("/api/admin/ai/profiles", {
			data: {
				name: PROFILE_NAME,
				connection_id: connection.id,
				model: "fixture-chat",
				capabilities: {
					tool_calling: false,
					image_input: false,
					pdf_input: false,
					source: "manual",
				},
				enabled_for_chat: true,
			},
		});
		await expectOk(profileResponse);
		const profile = (await profileResponse.json()) as CreatedRecord;

		return { connectionId: connection.id, profileId: profile.id };
	} catch (error) {
		if (connectionId) {
			await expectDeleted(
				await api.delete(`/api/admin/ai/connections/${connectionId}`),
			);
		}
		throw error;
	}
}

async function createFixtureAgent(api: AuthedApi, profileId: string) {
	const response = await api.post("/api/agents", {
		data: {
			name: AGENT_NAME,
			description: "Operational reports usage fixture.",
			system_prompt: "Reply only with ok.",
			channels: ["chat"],
			access_level: "private",
			role_ids: [],
			system_tools: [],
			knowledge_sources: [],
			delegated_agent_ids: [],
			llm_profile_id: profileId,
		},
	});
	await expectOk(response);
	return (await response.json()) as CreatedRecord;
}

async function createConversation(api: AuthedApi, agentId: string) {
	const response = await api.post("/api/chat/conversations", {
		data: {
			agent_id: agentId,
			title: CONVERSATION_TITLE,
			channel: "chat",
		},
	});
	await expectOk(response);
	return (await response.json()) as CreatedRecord;
}

async function runChatTurn(api: AuthedApi, conversationId: string) {
	const response = await api.post("/api/chat/runs", {
		data: {
			conversation_id: conversationId,
			content: USER_PROMPT,
			attachment_ids: [],
		},
	});
	await expectOk(response);
	const run = (await response.json()) as { run_id: string };
	const startedAt = Date.now();
	for (;;) {
		const stateResponse = await api.get(
			`/api/chat/conversations/${conversationId}/state`,
		);
		await expectOk(stateResponse);
		const state = (await stateResponse.json()) as {
			active_run: { id: string; status: string; error: string | null } | null;
			messages: Array<{ role: string; content: string | null }>;
		};
		if (
			state.messages.some(
				(message) => message.role === "assistant" && message.content === "ok",
			)
		) {
			return;
		}
		if (
			state.active_run?.id === run.run_id &&
			state.active_run.status === "failed"
		) {
			throw new Error(state.active_run.error ?? "unknown chat run error");
		}
		if (Date.now() - startedAt > 30_000) {
			throw new Error(`Chat run ${run.run_id} did not complete after 30000ms`);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
}

async function readROIByWorkflow(api: AuthedApi) {
	const { start, end } = reportDateRange();
	const response = await api.get("/api/reports/roi/by-workflow", {
		params: { start_date: start, end_date: end, limit: 100 },
	});
	await expectOk(response);
	return (await response.json()) as ROIByWorkflow;
}

async function readUsageReport(api: AuthedApi, source: "all" | "chat" = "all") {
	const { start, end } = reportDateRange();
	const response = await api.get("/api/reports/usage", {
		params: { start_date: start, end_date: end, source },
	});
	await expectOk(response);
	return (await response.json()) as UsageReport;
}

async function waitForReportRows(api: AuthedApi) {
	const startedAt = Date.now();
	for (;;) {
		const [roi, usage] = await Promise.all([
			readROIByWorkflow(api),
			readUsageReport(api, "chat"),
		]);
		const roiRow = roi.workflows.find(
			(workflow) => workflow.workflow_name === WORKFLOW_NAME,
		);
		const usageRow = usage.by_conversation.find(
			(conversation) => conversation.conversation_title === CONVERSATION_TITLE,
		);
		if (roiRow && usageRow) return { roiRow, usageRow };
		if (Date.now() - startedAt > 30_000) {
			throw new Error(
				"Seeded operational report rows did not appear after 30000ms",
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
}

test.describe("Operational reports acceptance (admin)", () => {
	let workflowId: string | null = null;
	let conversationId: string | null = null;
	let agentId: string | null = null;
	let profileId: string | null = null;
	let connectionId: string | null = null;

	test.beforeAll(async ({ api }) => {
		workflowId = await registerWorkflow(api);
		const executionId = await executeWorkflow(api, workflowId);
		await waitForTerminalSuccess(api, executionId);

		const profile = await createFixtureProfile(api);
		profileId = profile.profileId;
		connectionId = profile.connectionId;
		const agent = await createFixtureAgent(api, profileId);
		agentId = agent.id;
		const conversation = await createConversation(api, agentId);
		conversationId = conversation.id;
		await runChatTurn(api, conversationId);

		const { roiRow, usageRow } = await waitForReportRows(api);
		expect(roiRow.execution_count).toBe(1);
		expect(roiRow.success_count).toBe(1);
		expect(roiRow.time_saved_per_execution).toBe(WORKFLOW_TIME_SAVED);
		expect(roiRow.value_per_execution).toBe(WORKFLOW_VALUE);
		expect(roiRow.total_time_saved).toBe(WORKFLOW_TIME_SAVED);
		expect(roiRow.total_value).toBe(WORKFLOW_VALUE);
		expect(usageRow.message_count).toBe(1);
		expect(usageRow.input_tokens).toBe(1);
		expect(usageRow.output_tokens).toBe(1);
	});

	test.afterAll(async ({ api }) => {
		if (conversationId) {
			await expectDeleted(
				await api.delete(`/api/chat/conversations/${conversationId}`),
			);
		}
		if (agentId) {
			await expectDeleted(await api.delete(`/api/agents/${agentId}`));
		}
		if (profileId) {
			await expectDeleted(await api.delete(`/api/admin/ai/profiles/${profileId}`));
		}
		if (connectionId) {
			await expectDeleted(
				await api.delete(`/api/admin/ai/connections/${connectionId}`),
			);
		}
		if (WORKFLOW_PATH) {
			await expectDeleted(
				await api.delete(
					`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
				),
			);
		}
	});

	test("dashboard, usage, and ROI reports show the seeded rows", async ({
		page,
		api,
	}) => {
		await page.setViewportSize({ width: 1440, height: 900 });
		const { roiRow, usageRow } = await waitForReportRows(api);

		const dashboardMetrics = page.waitForResponse((response) =>
			/\/api\/metrics(?:\?|$)/.test(response.url()) && response.ok(),
		);
		await page.goto("/dashboard");
		await dashboardMetrics;
		await expect(
			page.getByRole("heading", { name: "Dashboard", exact: true }),
		).toBeVisible({ timeout: 10_000 });
		await expect(page.getByText("Success Rate")).toBeVisible();
		await expect(page.getByText("Value (24h)")).toBeVisible();
		await expect(page.getByRole("radio", { name: "Last 24 hours" })).toBeVisible();

		await Promise.all([
			page.waitForResponse((response) =>
				response.url().includes("/api/reports/roi/summary") && response.ok(),
			),
			page.waitForResponse((response) =>
				response.url().includes("/api/reports/roi/by-workflow") && response.ok(),
			),
			page.waitForResponse((response) =>
				response.url().includes("/api/reports/roi/trends") && response.ok(),
			),
			page.getByRole("link", { name: /Value \(24h\)/ }).click(),
		]);
		await expect(page).toHaveURL(/\/reports\/roi$/);
		await expect(
			page.getByRole("heading", { name: "ROI Reports", exact: true }),
		).toBeVisible({ timeout: 10_000 });
		await expect(page.getByRole("cell", { name: WORKFLOW_NAME, exact: true })).toBeVisible();
		const roiRecord = page
			.getByRole("row")
			.filter({ hasText: WORKFLOW_NAME })
			.first();
		await expect(roiRecord).toContainText(String(roiRow.execution_count));
		await expect(roiRecord).toContainText(
			(roiRow.total_time_saved / 60).toFixed(2),
		);
		await expect(roiRecord).toContainText(
			roiRow.total_value.toLocaleString("en-US", {
				style: "currency",
				currency: "USD",
			}),
		);

		const usageReport = page.waitForResponse((response) =>
			response.url().includes("/api/reports/usage") && response.ok(),
		);
		await page.goto("/reports/usage");
		await usageReport;
		await expect(
			page.getByRole("heading", { name: "Usage", exact: true }),
		).toBeVisible({ timeout: 10_000 });
		await expect(page.getByText("Total AI Cost")).toBeVisible();
		await expect(page.getByText("Usage by Conversation")).toBeVisible();

		const chatUsageResponsePromise = page.waitForResponse(
			(response) =>
				response.url().includes("/api/reports/usage") &&
				response.url().includes("source=chat") &&
				response.ok(),
		);
		await page.getByRole("tab", { name: "Chat" }).click();
		await chatUsageResponsePromise;
		await expect(page.getByRole("row").filter({ hasText: CONVERSATION_TITLE })).toBeVisible();
		const usageRecord = page
			.getByRole("row")
			.filter({ hasText: CONVERSATION_TITLE })
			.first();
		await expect(usageRecord).toContainText(String(usageRow.message_count));
		await expect(usageRecord).toContainText(
			String(usageRow.input_tokens + usageRow.output_tokens),
		);
		await expect(page.getByText("Usage by Workflow")).not.toBeVisible();
	});
});
