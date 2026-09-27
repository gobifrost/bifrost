import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
	test,
	expect,
	request as playwrightRequest,
	type Page,
	type APIRequestContext,
	type APIResponse,
	type Response,
} from "@playwright/test";

const API_URL = process.env.TEST_API_URL || "http://api:8000";
const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const WORKFLOW_PATH = `e2e_member_form_${UNIQUE}.py`;
const WORKFLOW_FN = `e2e_member_form_${UNIQUE}`;
const ASSIGNED_FORM_NAME = `Assigned member form ${UNIQUE}`;
const FORBIDDEN_FORM_NAME = `Forbidden member form ${UNIQUE}`;

const WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${WORKFLOW_FN}")
async def ${WORKFLOW_FN}(request_id: str, requester_email: str, priority: str = "normal"):
    return {
        "acceptance_marker": "${UNIQUE}",
        "request_id": request_id,
        "requester_email": requester_email,
        "priority": priority,
    }
`;

async function expectOk(response: APIResponse | Response) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectNoContent(response: APIResponse | Response) {
	expect(response.status(), await response.text()).toBe(204);
}

async function pollExecutionResult(
	api: APIRequestContext,
	executionId: string,
) {
	for (let attempt = 0; attempt < 40; attempt += 1) {
		const response = await api.get(`/api/executions/${executionId}`);
		await expectOk(response);
		const execution = (await response.json()) as {
			status: string;
			result?: Record<string, unknown> | null;
			input_data?: Record<string, unknown> | null;
			error_message?: string | null;
		};
		if (execution.status === "Success") return execution;
		if (
			["Failed", "CompletedWithErrors", "Timeout", "Cancelled"].includes(
				execution.status,
			)
		) {
			throw new Error(
				`Execution ${executionId} ended as ${execution.status}: ${execution.error_message ?? ""}`,
			);
		}
		await new Promise((resolve) => setTimeout(resolve, 500));
	}
	throw new Error(`Execution ${executionId} did not finish in time`);
}

async function expectDeleted(response: APIResponse) {
	expect([200, 204, 404]).toContain(response.status());
}

async function submitAssignedFormAndVerifyResult(
	page: Page,
	adminApi: APIRequestContext,
	viewportLabel: "desktop" | "mobile",
	assignedFormId: string,
) {
	const requestId = `REQ-${viewportLabel}-${UNIQUE}`;
	const requesterEmail = `${viewportLabel}-alice@example.com`;

	await page.goto("/forms");

	await expect(
		page.getByRole("heading", { name: /forms/i }).first(),
	).toBeVisible({ timeout: 10000 });
	await expect(page.getByText(ASSIGNED_FORM_NAME)).toBeVisible();
	await expect(page.getByText(FORBIDDEN_FORM_NAME)).toHaveCount(0);
	await expect(
		page.getByRole("button", { name: /create|new form/i }),
	).toHaveCount(0);
	await expect(page.getByRole("button", { name: /edit/i })).toHaveCount(0);
	await expect(
		page.getByRole("button", { name: /delete|remove/i }),
	).toHaveCount(0);

	await page
		.locator('[data-slot="card"]')
		.filter({ hasText: ASSIGNED_FORM_NAME })
		.getByRole("link", { name: ASSIGNED_FORM_NAME })
		.click();
	await expect(page).toHaveURL(new RegExp(`/execute/${assignedFormId}$`));

	await expect(
		page.getByRole("heading", { name: ASSIGNED_FORM_NAME }),
	).toBeVisible();
	await page.getByLabel("Request ID").fill(requestId);
	await page.getByLabel("Requester Email").fill(requesterEmail);
	await page.getByRole("combobox", { name: "Priority *" }).click();
	await page.getByRole("option", { name: "Urgent" }).click();

	const submission = page.waitForResponse(
		(response) =>
			response
				.url()
				.includes(`/api/forms/${assignedFormId}/submissions`) &&
			response.request().method() === "POST",
	);
	await page.getByRole("button", { name: "Submit" }).click();
	const submissionResponse = await submission;
	await expectOk(submissionResponse);
	const submissionBody = (await submissionResponse.json()) as {
		mode: string;
		execution_id: string;
	};
	expect(submissionBody.mode).toBe("execution");
	expect(submissionBody.execution_id).toMatch(
		/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
	);

	await expect(page).toHaveURL(
		new RegExp(`/history/${submissionBody.execution_id}$`),
		{ timeout: 10000 },
	);
	await page.getByRole("tab", { name: "Result" }).click();
	const resultPanel = page.getByRole("tabpanel", { name: "Result" });
	await expect(resultPanel).toBeVisible({ timeout: 30000 });
	await expect(resultPanel.getByText(UNIQUE).first()).toBeVisible({
		timeout: 30000,
	});
	await expect(resultPanel.getByText(requesterEmail).first()).toBeVisible();
	await expect(resultPanel.getByText(requestId).first()).toBeVisible();
	await expect(resultPanel.getByText("urgent").first()).toBeVisible();

	const execution = await pollExecutionResult(
		adminApi,
		submissionBody.execution_id,
	);
	expect(execution.input_data).toMatchObject({
		request_id: requestId,
		requester_email: requesterEmail,
		priority: "urgent",
	});
	expect(execution.result).toMatchObject({
		acceptance_marker: UNIQUE,
		request_id: requestId,
		requester_email: requesterEmail,
		priority: "urgent",
	});
}

test.describe("Form Acceptance for Org Users", () => {
	// These cases share one registered workflow and role assignment. With
	// fullyParallel inheritance, beforeAll reruns for each case in a worker
	// while module-level names are reused, racing cleanup and registration.
	// Keep this fixture group together; other spec files still run in parallel.
	test.describe.configure({ mode: "default" });
	let adminApi: APIRequestContext;
	let assignedRoleId: string;
	let forbiddenRoleId: string;
	let assignedFormId: string;
	let forbiddenFormId: string;
	let memberUserId: string;

	test.beforeAll(async () => {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as {
			platform_admin: { accessToken: string };
			org1_user: { userId: string; organizationId: string };
		};
		memberUserId = credentials.org1_user.userId;
		const memberOrganizationId = credentials.org1_user.organizationId;

		adminApi = await playwrightRequest.newContext({
			baseURL: API_URL,
			extraHTTPHeaders: {
				Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
			},
		});

		const write = await adminApi.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				content: WORKFLOW_SOURCE,
				encoding: "utf-8",
			},
		});
		await expectOk(write);

		const registration = await adminApi.post("/api/workflows/register", {
			data: {
				path: WORKFLOW_PATH,
				function_name: WORKFLOW_FN,
				organization_id: memberOrganizationId,
			},
		});
		await expectOk(registration);

		const workflows = await adminApi.get("/api/workflows");
		await expectOk(workflows);
		const workflow = (
			(await workflows.json()) as Array<{ id: string; name: string }>
		).find((item) => item.name === WORKFLOW_FN);
		expect(workflow).toBeTruthy();

		const assignedRole = await adminApi.post("/api/roles", {
			data: {
				name: `Member Form Acceptance ${UNIQUE}`,
				description: "Allows the seeded org user to launch the form",
			},
		});
		await expectOk(assignedRole);
		assignedRoleId = ((await assignedRole.json()) as { id: string }).id;

		const forbiddenRole = await adminApi.post("/api/roles", {
			data: {
				name: `Member Form Forbidden ${UNIQUE}`,
				description:
					"Intentionally not assigned to the seeded org user",
			},
		});
		await expectOk(forbiddenRole);
		forbiddenRoleId = ((await forbiddenRole.json()) as { id: string }).id;

		const assignUser = await adminApi.post(
			`/api/roles/${assignedRoleId}/users`,
			{ data: { user_ids: [memberUserId] } },
		);
		await expectNoContent(assignUser);

		const assignedRoleUsers = await adminApi.get(
			`/api/roles/${assignedRoleId}/users`,
		);
		await expectOk(assignedRoleUsers);
		expect(
			((await assignedRoleUsers.json()) as { user_ids: string[] })
				.user_ids,
		).toContain(memberUserId);

		const formSchema = {
			fields: [
				{
					name: "request_id",
					label: "Request ID",
					type: "text",
					required: true,
				},
				{
					name: "requester_email",
					label: "Requester Email",
					type: "email",
					required: true,
				},
				{
					name: "priority",
					label: "Priority",
					type: "select",
					required: true,
					options: [
						{ value: "normal", label: "Normal" },
						{ value: "urgent", label: "Urgent" },
					],
				},
			],
		};

		const assignedForm = await adminApi.post("/api/forms", {
			data: {
				name: ASSIGNED_FORM_NAME,
				description:
					"A deterministic member submission acceptance form",
				workflow_id: workflow!.id,
				organization_id: memberOrganizationId,
				form_schema: formSchema,
				access_level: "role_based",
				role_ids: [assignedRoleId],
			},
		});
		await expectOk(assignedForm);
		assignedFormId = ((await assignedForm.json()) as { id: string }).id;

		const forbiddenForm = await adminApi.post("/api/forms", {
			data: {
				name: FORBIDDEN_FORM_NAME,
				description: "The seeded member must not see this form",
				workflow_id: workflow!.id,
				organization_id: memberOrganizationId,
				form_schema: formSchema,
				access_level: "role_based",
				role_ids: [forbiddenRoleId],
			},
		});
		await expectOk(forbiddenForm);
		forbiddenFormId = ((await forbiddenForm.json()) as { id: string }).id;

		const memberVisibleForms = await adminApi.get(
			`/api/users/${memberUserId}/forms`,
		);
		await expectOk(memberVisibleForms);
		const visibleFormIds = (
			(await memberVisibleForms.json()) as { form_ids: string[] }
		).form_ids;
		expect(visibleFormIds).toContain(assignedFormId);
		expect(visibleFormIds).not.toContain(forbiddenFormId);
	});

	test.afterAll(async () => {
		if (!adminApi) return;
		if (assignedRoleId) {
			await expectDeleted(
				await adminApi.delete(
					`/api/roles/${assignedRoleId}/users/${memberUserId}`,
				),
			);
		}
		if (assignedFormId) {
			await expectDeleted(
				await adminApi.delete(`/api/forms/${assignedFormId}`),
			);
		}
		if (forbiddenFormId) {
			await expectDeleted(
				await adminApi.delete(`/api/forms/${forbiddenFormId}`),
			);
		}
		if (assignedRoleId) {
			await expectDeleted(
				await adminApi.delete(`/api/roles/${assignedRoleId}`),
			);
		}
		if (forbiddenRoleId) {
			await expectDeleted(
				await adminApi.delete(`/api/roles/${forbiddenRoleId}`),
			);
		}
		await expectDeleted(
			await adminApi.delete(
				`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
			),
		);
		await adminApi.dispose();
	});

	test(
		"[FORM-01 desktop] member submits assigned form and sees workflow result",
		{ tag: "@smoke" },
		async ({ page }) => {
			await submitAssignedFormAndVerifyResult(
				page,
				adminApi,
				"desktop",
				assignedFormId,
			);
		},
	);

	test("[FORM-01 mobile] member submits assigned form and sees workflow result", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await submitAssignedFormAndVerifyResult(
			page,
			adminApi,
			"mobile",
			assignedFormId,
		);
	});

	test("[FORM-ACCESS-01 desktop] member cannot see or open an unassigned role-based form", async ({
		page,
	}) => {
		await page.goto("/forms");

		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({ timeout: 10000 });
		await expect(page.getByText(ASSIGNED_FORM_NAME)).toBeVisible();
		await expect(page.getByText(FORBIDDEN_FORM_NAME)).toHaveCount(0);

		const denial = page.waitForResponse(
			(response) =>
				response
					.url()
					.includes(`/api/forms/${forbiddenFormId}/runtime`) &&
				response.request().method() === "GET",
		);
		await page.goto(`/execute/${forbiddenFormId}`);
		expect([403, 404]).toContain((await denial).status());
		await expect(page.getByRole("alert")).toContainText(
			"Form unavailable",
			{ timeout: 10000 },
		);
		await expect(
			page.getByLabel("Request ID", { exact: true }),
		).toHaveCount(0);
		await expect(page.getByText(FORBIDDEN_FORM_NAME)).toHaveCount(0);
	});
});
