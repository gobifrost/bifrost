import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const WORKFLOW_PATH = `e2e_form_fields_${UNIQUE}.py`;
const WORKFLOW_FN = `e2e_form_fields_${UNIQUE}`;
const FORM_NAME = `Field designer acceptance form ${UNIQUE}`;
const ADDED_FIELD_NAME = "notes";
const ADDED_FIELD_LABEL = "Detailed Notes";
const ADDED_FIELD_PLACEHOLDER = `Add field placeholder ${UNIQUE}`;
const ADDED_FIELD_HELP = `Persisted help text ${UNIQUE}`;

const WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${WORKFLOW_FN}")
async def ${WORKFLOW_FN}(title: str, notes: str = ""):
    return {"title": title, "notes": notes}
`;

type ApiResponse = Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">;

type PersistedForm = {
	form_schema?: {
		fields?: Array<{
			name?: string;
			label?: string;
			type?: string;
			required?: boolean;
			placeholder?: string | null;
			help_text?: string | null;
		}>;
	};
};

async function expectOk(response: ApiResponse) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
) {
	expect([200, 204, 404]).toContain(response.status());
}

async function cleanupAcceptanceFixtures(
	api: AuthedApi,
	formId?: string,
): Promise<Error[]> {
	const cleanupErrors: Error[] = [];
	const runCleanup = async (label: string, cleanup: () => Promise<void>) => {
		try {
			await cleanup();
		} catch (error) {
			cleanupErrors.push(
				error instanceof Error
					? new Error(`${label}: ${error.message}`)
					: new Error(`${label}: ${String(error)}`),
			);
		}
	};

	if (formId) {
		await runCleanup("delete form", async () => {
			await expectDeleted(await api.delete(`/api/forms/${formId}`));
		});
	}
	await runCleanup("delete workflow source", async () => {
		await expectDeleted(
			await api.delete(
				`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
			),
		);
	});

	return cleanupErrors;
}

test("[FORM-03 desktop] designer adds and persists a configured form field", async ({
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
		const write = await api.put("/api/files/editor/content", {
			data: {
				path: WORKFLOW_PATH,
				content: WORKFLOW_SOURCE,
				encoding: "utf-8",
			},
		});
		await expectOk(write);

		const registration = await api.post("/api/workflows/register", {
			data: {
				path: WORKFLOW_PATH,
				function_name: WORKFLOW_FN,
				organization_id: organizationId,
			},
		});
		await expectOk(registration);

		const workflows = await api.get("/api/workflows", {
			params: { scope: organizationId },
		});
		await expectOk(workflows);
		const workflow = (
			(await workflows.json()) as Array<{ id: string; name: string }>
		).find((item) => item.name === WORKFLOW_FN);
		expect(workflow).toBeTruthy();

		const created = await api.post("/api/forms", {
			data: {
				name: FORM_NAME,
				description: "Base form before field designer edit",
				workflow_id: workflow!.id,
				organization_id: organizationId,
				form_schema: {
					fields: [
						{
							name: "title",
							label: "Title",
							type: "text",
							required: true,
						},
					],
				},
				access_level: "authenticated",
			},
		});
		await expectOk(created);
		formId = ((await created.json()) as { id: string }).id;

		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10000,
		});
		await expect(
			page
				.locator('[data-field-card="title"]')
				.getByText("Title *", { exact: true }),
		).toBeVisible();

		await page.getByRole("button", { name: "Add Field" }).click();
		const dialog = page.getByRole("dialog", { name: "Add Field" });
		await expect(dialog).toBeVisible();
		await dialog.getByLabel("Field Name *").fill(ADDED_FIELD_NAME);
		await dialog.getByLabel("Label *").fill(ADDED_FIELD_LABEL);
		await dialog.getByLabel("Field Type").click();
		await page.getByRole("option", { name: "Textarea" }).click();
		await dialog.getByRole("button", { name: "Optional" }).click();
		await dialog.getByLabel("Placeholder").fill(ADDED_FIELD_PLACEHOLDER);
		await dialog.getByLabel("Help Text").fill(ADDED_FIELD_HELP);
		await dialog
			.getByRole("button", { name: "Add Field", exact: true })
			.click();
		await expect(dialog).toBeHidden();

		await expect(
			page.getByText(ADDED_FIELD_LABEL, { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText(ADDED_FIELD_NAME, { exact: true }),
		).toBeVisible();
		await expect(
			page
				.locator(`[data-field-card="${ADDED_FIELD_NAME}"]`)
				.getByText("Text Area", { exact: true }),
		).toBeVisible();

		const saveResponse = page.waitForResponse(
			(response) =>
				response.url().includes(`/api/forms/${formId}`) &&
				response.request().method() === "PATCH",
		);
		await page.getByRole("button", { name: "Save" }).click();
		await expectOk(await saveResponse);

		const updated = await api.get(`/api/forms/${formId}`);
		await expectOk(updated);
		const updatedFields = ((await updated.json()) as PersistedForm)
			.form_schema?.fields;
		expect(updatedFields).toEqual([
			expect.objectContaining({
				name: "title",
				label: "Title",
				type: "text",
				required: true,
			}),
			expect.objectContaining({
				name: ADDED_FIELD_NAME,
				label: ADDED_FIELD_LABEL,
				type: "textarea",
				required: false,
				placeholder: ADDED_FIELD_PLACEHOLDER,
				help_text: ADDED_FIELD_HELP,
			}),
		]);

		await expect(page).toHaveURL(/\/forms$/);
		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10000,
		});
		await expect(
			page.getByText(ADDED_FIELD_LABEL, { exact: true }),
		).toBeVisible();

		await page.getByRole("tab", { name: "Preview" }).click();
		const previewPanel = page.getByRole("tabpanel", { name: "Preview" });
		await expect(previewPanel).toBeVisible();
		await expect(previewPanel.getByText(FORM_NAME)).toBeVisible();
		const previewField = previewPanel.getByLabel(ADDED_FIELD_LABEL);
		await expect(previewField).toBeVisible();
		await expect(previewField).toHaveAttribute(
			"placeholder",
			ADDED_FIELD_PLACEHOLDER,
		);
		await expect(previewPanel.getByText(ADDED_FIELD_HELP)).toBeVisible();
	} catch (error) {
		originalError = error;
	} finally {
		cleanupErrors = await cleanupAcceptanceFixtures(api, formId);
	}

	if (originalError) throw originalError;
	if (cleanupErrors.length > 0) {
		throw new Error(cleanupErrors.map((error) => error.message).join("\n"));
	}
});
