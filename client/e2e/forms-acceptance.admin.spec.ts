import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const WORKFLOW_PATH = `e2e_designer_form_${UNIQUE}.py`;
const WORKFLOW_FN = `e2e_designer_form_${UNIQUE}`;
const ORIGINAL_FORM_NAME = `Designer acceptance form ${UNIQUE}`;
const EDITED_FORM_NAME = `${ORIGINAL_FORM_NAME} edited`;
const EDITED_DESCRIPTION = `Saved and reopened from designer ${UNIQUE}`;

const WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${WORKFLOW_FN}")
async def ${WORKFLOW_FN}(summary: str):
    return {"summary": summary}
`;

async function expectOk(
	response: Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">,
) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
) {
	expect([200, 204, 404]).toContain(response.status());
}

test("[FORM-02 desktop] designer saves and reopens form metadata", async ({
	page,
	api,
}) => {
	const write = await api.put("/api/files/editor/content", {
		data: {
			path: WORKFLOW_PATH,
			content: WORKFLOW_SOURCE,
			encoding: "utf-8",
		},
	});
	await expectOk(write);

	const registration = await api.post("/api/workflows/register", {
		data: { path: WORKFLOW_PATH, function_name: WORKFLOW_FN },
	});
	await expectOk(registration);

	const workflows = await api.get("/api/workflows");
	await expectOk(workflows);
	const workflow = (
		(await workflows.json()) as Array<{ id: string; name: string }>
	).find((item) => item.name === WORKFLOW_FN);
	expect(workflow).toBeTruthy();

	const created = await api.post("/api/forms", {
		data: {
			name: ORIGINAL_FORM_NAME,
			description: "Original designer metadata",
			workflow_id: workflow!.id,
			form_schema: {
				fields: [
					{
						name: "summary",
						label: "Summary",
						type: "text",
						required: true,
					},
				],
			},
			access_level: "authenticated",
		},
	});
	await expectOk(created);
	const formId = ((await created.json()) as { id: string }).id;

	try {
		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: ORIGINAL_FORM_NAME }),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByText("Original designer metadata"),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Edit Summary", exact: true }),
		).toBeVisible();

		await page.getByTitle("Edit Info").click();
		const dialog = page.getByRole("dialog", { name: "Form Information" });
		await expect(dialog).toBeVisible();
		await dialog.getByLabel("Form Name *").fill(EDITED_FORM_NAME);
		await dialog.getByLabel("Description").fill(EDITED_DESCRIPTION);
		await dialog.getByRole("button", { name: "Save" }).click();
		await expect(dialog).toHaveCount(0);

		const update = page.waitForResponse(
			(response) =>
				response.url().includes(`/api/forms/${formId}`) &&
				response.request().method() === "PATCH",
		);
		await page.getByRole("button", { name: "Save" }).click();
		const updateResponse = await update;
		await expectOk(updateResponse);
		// Check the persisted result; Chromium may release request bodies after navigation.
		const updated = (await updateResponse.json()) as {
			name: string;
			description: string;
			form_schema?: {
				fields?: Array<{ name?: string; label?: string }>;
				layout?: unknown;
			};
		};
		expect(updated.form_schema?.layout ?? null).toBeNull();
		expect(updated.name).toBe(EDITED_FORM_NAME);
		expect(updated.description).toBe(EDITED_DESCRIPTION);
		expect(updated.form_schema?.fields).toEqual([
			expect.objectContaining({ name: "summary", label: "Summary" }),
		]);

		await expect(page).toHaveURL(/\/forms$/);
		await expect(page.getByText(EDITED_FORM_NAME)).toBeVisible({
			timeout: 10000,
		});
		await expect(
			page.getByText(ORIGINAL_FORM_NAME, { exact: true }),
		).toHaveCount(0);

		await page.goto(`/forms/${formId}/edit`);
		await expect(
			page.getByRole("heading", { name: EDITED_FORM_NAME }),
		).toBeVisible({ timeout: 10000 });
		await expect(page.getByText(EDITED_DESCRIPTION)).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Edit Summary", exact: true }),
		).toBeVisible();

		const fetched = await api.get(`/api/forms/${formId}`);
		await expectOk(fetched);
		const persisted = (await fetched.json()) as {
			name: string;
			description: string;
			form_schema?: { fields?: Array<{ name?: string; label?: string }> };
		};
		expect(persisted.name).toBe(EDITED_FORM_NAME);
		expect(persisted.description).toBe(EDITED_DESCRIPTION);
		expect(persisted.form_schema?.fields).toEqual([
			expect.objectContaining({ name: "summary", label: "Summary" }),
		]);
	} finally {
		await expectDeleted(await api.delete(`/api/forms/${formId}`));
		await expectDeleted(
			await api.delete(
				`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
			),
		);
	}
});
