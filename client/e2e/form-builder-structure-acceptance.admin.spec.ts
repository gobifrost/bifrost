import { readFileSync } from "fs";
import { resolve } from "path";
import type { Page } from "./fixtures/api-fixture";
import { expect, test, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const INITIAL_WORKFLOW_PATH = `e2e_form_builder_initial_${UNIQUE}.py`;
const INITIAL_WORKFLOW_FN = `e2e_form_builder_initial_${UNIQUE}`;
const TARGET_WORKFLOW_PATH = `e2e_form_builder_target_${UNIQUE}.py`;
const TARGET_WORKFLOW_FN = `e2e_form_builder_target_${UNIQUE}`;
const FORM_NAME = `Builder structure acceptance ${UNIQUE}`;
const FORM_DESCRIPTION = `Reorder and binding acceptance ${UNIQUE}`;

const INITIAL_WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${INITIAL_WORKFLOW_FN}")
async def ${INITIAL_WORKFLOW_FN}(first_value: str):
    return {"first_value": first_value}
`;

const TARGET_WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${TARGET_WORKFLOW_FN}")
async def ${TARGET_WORKFLOW_FN}(second_value: str):
    return {"second_value": second_value}
`;

type ApiResponse = Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">;

type PersistedForm = {
	id: string;
	name: string;
	description?: string | null;
	workflow_id?: string | null;
	is_active: boolean;
	form_schema?: {
		fields?: Array<{
			name?: string;
			label?: string;
			type?: string;
			required?: boolean;
		}>;
	} | null;
};

async function expectOk(response: ApiResponse, label: string) {
	expect(response.ok(), `${label}: ${await response.text()}`).toBe(true);
}

async function expectStatus(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
	allowed: number[],
	label: string,
) {
	expect(
		allowed,
		`${label}: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
}

async function writeAndRegisterWorkflow(
	api: AuthedApi,
	path: string,
	functionName: string,
	source: string,
	organizationId: string,
): Promise<string> {
	const write = await api.put("/api/files/editor/content", {
		data: { path, content: source, encoding: "utf-8" },
	});
	await expectOk(write, `write workflow ${functionName}`);

	const registration = await api.post("/api/workflows/register", {
		data: {
			path,
			function_name: functionName,
			organization_id: organizationId,
		},
	});
	await expectOk(registration, `register workflow ${functionName}`);
	const registered = (await registration.json()) as { id?: string };
	if (registered.id) return registered.id;

	const workflows = await api.get("/api/workflows", {
		params: { scope: organizationId },
	});
	await expectOk(workflows, `read workflow ${functionName}`);
	const workflow = (
		(await workflows.json()) as Array<{ id: string; name: string }>
	).find((item) => item.name === functionName);
	expect(workflow, `registered workflow ${functionName} exists`).toBeTruthy();
	return workflow!.id;
}

async function readForm(
	api: AuthedApi,
	formId: string,
): Promise<PersistedForm> {
	const response = await api.get(`/api/forms/${formId}`);
	await expectOk(response, "read persisted form");
	return (await response.json()) as PersistedForm;
}

async function saveBuilderForm(api: AuthedApi, page: Page, formId: string) {
	const saveResponse = page.waitForResponse(
		(response) =>
			response.url().includes(`/api/forms/${formId}`) &&
			response.request().method() === "PATCH",
	);
	await page.getByRole("button", { name: "Save" }).click();
	await expectOk(await saveResponse, "save form builder changes");
	await expect(page).toHaveURL(/\/forms$/);
}

async function cleanupFixtures(
	api: AuthedApi,
	formId?: string,
): Promise<Error[]> {
	const errors: Error[] = [];
	const cleanup = async (label: string, action: () => Promise<void>) => {
		try {
			await action();
		} catch (error) {
			errors.push(
				error instanceof Error
					? new Error(`${label}: ${error.message}`)
					: new Error(`${label}: ${String(error)}`),
			);
		}
	};

	if (formId) {
		await cleanup("delete form", async () => {
			await expectStatus(
				await api.delete(`/api/forms/${formId}`),
				[200, 204, 404],
				"delete form cleanup",
			);
		});
	}
	for (const path of [INITIAL_WORKFLOW_PATH, TARGET_WORKFLOW_PATH]) {
		await cleanup(`delete workflow source ${path}`, async () => {
			await expectStatus(
				await api.delete(
					`/api/files/editor?path=${encodeURIComponent(path)}`,
				),
				[200, 204, 404],
				`delete workflow source ${path}`,
			);
		});
	}
	return errors;
}

test("[FORM-BUILDER-STRUCTURE-01 desktop] designer rebinds workflow, reorders fields, deletes a field, and persists structure", async ({
	page,
	api,
}) => {
	const credentials = JSON.parse(
		readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
	) as { org1_user: { organizationId: string } };
	const organizationId = credentials.org1_user.organizationId;
	let formId: string | undefined;
	let originalError: unknown;
	let cleanupErrors: Error[] = [];

	try {
		const initialWorkflowId = await writeAndRegisterWorkflow(
			api,
			INITIAL_WORKFLOW_PATH,
			INITIAL_WORKFLOW_FN,
			INITIAL_WORKFLOW_SOURCE,
			organizationId,
		);
		const targetWorkflowId = await writeAndRegisterWorkflow(
			api,
			TARGET_WORKFLOW_PATH,
			TARGET_WORKFLOW_FN,
			TARGET_WORKFLOW_SOURCE,
			organizationId,
		);

		const created = await api.post("/api/forms", {
			data: {
				name: FORM_NAME,
				description: FORM_DESCRIPTION,
				workflow_id: initialWorkflowId,
				organization_id: organizationId,
				form_schema: {
					fields: [
						{
							name: "first_value",
							label: "First Value",
							type: "text",
							required: true,
						},
						{
							name: "second_value",
							label: "Second Value",
							type: "text",
							required: true,
						},
					],
				},
				access_level: "authenticated",
			},
		});
		await expectOk(created, "create form builder fixture");
		formId = ((await created.json()) as { id: string }).id;

		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10_000,
		});
		await expect(
			page.getByText("First Value", { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("Second Value", { exact: true }),
		).toBeVisible();

		await page.getByTitle("Edit Info").click();
		const infoDialog = page.getByRole("dialog", {
			name: "Form Information",
		});
		await expect(infoDialog).toBeVisible();
		await infoDialog
			.getByRole("combobox")
			.filter({ hasText: INITIAL_WORKFLOW_FN })
			.click();
		await page.getByRole("option", { name: TARGET_WORKFLOW_FN }).click();
		await infoDialog.getByRole("button", { name: "Save" }).click();
		await expect(infoDialog).toHaveCount(0);
		await expect(
			page.getByText(targetWorkflowId, { exact: true }),
		).toBeVisible();

		await page.getByRole("button", { name: "Move Second Value" }).click();
		await page.getByRole("menuitem", { name: "Move up" }).click();
		await expect(
			page.getByText("Second Value moved to position 1 of 2."),
		).toBeAttached();

		await saveBuilderForm(api, page, formId);

		let persisted = await readForm(api, formId);
		expect(persisted.workflow_id).toBe(targetWorkflowId);
		expect(persisted.form_schema?.fields).toEqual([
			expect.objectContaining({
				name: "second_value",
				label: "Second Value",
				type: "text",
				required: true,
			}),
			expect.objectContaining({
				name: "first_value",
				label: "First Value",
				type: "text",
				required: true,
			}),
		]);

		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10_000,
		});
		await expect(
			page.getByText(targetWorkflowId, { exact: true }),
		).toBeVisible();
		const fieldMoveButtons = page.getByRole("button", { name: /^Move / });
		await expect(fieldMoveButtons).toHaveCount(2);
		await expect(fieldMoveButtons.first()).toHaveAccessibleName(
			"Move Second Value",
		);
		await expect(fieldMoveButtons.nth(1)).toHaveAccessibleName(
			"Move First Value",
		);

		await page.getByRole("button", { name: "Delete First Value" }).click();
		const removeDialog = page.getByRole("alertdialog", {
			name: "Remove Field",
		});
		await expect(removeDialog).toBeVisible();
		await removeDialog
			.getByRole("button", { name: "Remove Field" })
			.click();
		await expect(removeDialog).toHaveCount(0);
		await expect(
			page.getByText("First Value", { exact: true }),
		).toHaveCount(0);
		await expect(
			page.getByText("Second Value", { exact: true }),
		).toBeVisible();

		await saveBuilderForm(api, page, formId);

		persisted = await readForm(api, formId);
		expect(persisted.workflow_id).toBe(targetWorkflowId);
		expect(persisted.form_schema?.fields).toEqual([
			expect.objectContaining({
				name: "second_value",
				label: "Second Value",
				type: "text",
				required: true,
			}),
		]);

		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10_000,
		});
		await expect(
			page.getByText(targetWorkflowId, { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("Second Value", { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("First Value", { exact: true }),
		).toHaveCount(0);

		await page.goto("/forms");
		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({
			timeout: 10_000,
		});
		await page
			.getByRole("button", { name: `${FORM_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Delete Form" }).click();
		const deleteDialog = page.getByRole("alertdialog", {
			name: "Deactivate form?",
		});
		await expect(deleteDialog).toBeVisible();
		await deleteDialog.getByRole("button", { name: "Delete Form" }).click();
		await expect(deleteDialog).toBeHidden();
		await expect(
			page.getByRole("region", { name: "Forms list" }),
		).toContainText("Disabled");
		persisted = await readForm(api, formId);
		expect(persisted.is_active).toBe(false);
		await page.reload();
		await expect(page.getByText(FORM_NAME, { exact: true })).toBeVisible();
		await expect(
			page.getByRole("region", { name: "Forms list" }),
		).toContainText("Disabled");
	} catch (error) {
		originalError = error;
	} finally {
		cleanupErrors = await cleanupFixtures(api, formId);
	}

	if (originalError) throw originalError;
	if (cleanupErrors.length > 0) {
		throw new Error(cleanupErrors.map((error) => error.message).join("\n"));
	}
});
