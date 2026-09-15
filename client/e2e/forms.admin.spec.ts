import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}_${Math.floor(Math.random() * 10_000)}`;
const WORKFLOW_PATH = `e2e_admin_form_browse_${UNIQUE}.py`;
const WORKFLOW_FN = `e2e_admin_form_browse_${UNIQUE}`;
const FORM_NAME = `Admin browse form ${UNIQUE}`;
const FORM_DESCRIPTION = `Searchable admin form fixture ${UNIQUE}`;

const WORKFLOW_SOURCE = `from bifrost import workflow

@workflow(name="${WORKFLOW_FN}")
async def ${WORKFLOW_FN}(summary: str):
    return {"summary": summary}
`;

type ApiResponse = Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">;

async function expectOk(response: ApiResponse) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectDeleted(
	response: Awaited<ReturnType<AuthedApi["delete"]>>,
) {
	expect([200, 204, 404]).toContain(response.status());
}

async function cleanupFixtures(
	api: AuthedApi,
	formId?: string,
): Promise<Error[]> {
	const errors: Error[] = [];
	const collect = async (label: string, action: () => Promise<void>) => {
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
		await collect("delete form", async () => {
			await expectDeleted(await api.delete(`/api/forms/${formId}`));
		});
	}
	await collect("delete workflow source", async () => {
		await expectDeleted(
			await api.delete(
				`/api/files/editor?path=${encodeURIComponent(WORKFLOW_PATH)}`,
			),
		);
	});

	return errors;
}

test("[FORM-BROWSE-01 desktop] admin searches and opens a seeded form", async ({
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
				description: FORM_DESCRIPTION,
				workflow_id: workflow!.id,
				organization_id: organizationId,
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
		formId = ((await created.json()) as { id: string }).id;

		const seeded = await api.get(`/api/forms/${formId}`);
		await expectOk(seeded);

		await page.goto("/forms");
		await expect(
			page.getByRole("heading", { name: /forms/i }).first(),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByRole("button", { name: "Create Form" }),
		).toBeVisible();

		await page.getByRole("radio", { name: "Table view" }).click();
		await page
			.getByPlaceholder(
				"Search forms by name, description, or workflow...",
			)
			.fill(FORM_NAME);

		const row = page.getByRole("row").filter({ hasText: FORM_NAME });
		await expect(row).toBeVisible();
		await expect(row.getByText(FORM_DESCRIPTION)).toBeVisible();
		await row.click();
		await expect(page).toHaveURL(new RegExp(`/execute/${formId}$`));
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10000,
		});

		await page.goto("/forms");
		await page.getByRole("radio", { name: "Table view" }).click();
		await page
			.getByPlaceholder(
				"Search forms by name, description, or workflow...",
			)
			.fill(FORM_NAME);
		await page
			.getByRole("row")
			.filter({ hasText: FORM_NAME })
			.getByRole("button", { name: `${FORM_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Edit Form" }).click();
		await expect(page).toHaveURL(new RegExp(`/forms/${formId}/edit$`));
		await expect(
			page.getByRole("heading", { name: FORM_NAME }),
		).toBeVisible({
			timeout: 10000,
		});
		await expect(page.getByText("Summary", { exact: true })).toBeVisible();

		await page.goto("/forms");
		await page.getByRole("button", { name: "Create Form" }).click();
		await expect(page).toHaveURL(/\/forms\/new$/);
		await expect(
			page.getByRole("heading", {
				name: "Form Information",
				exact: true,
			}),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByRole("textbox", { name: "Form Name *", exact: true }),
		).toBeEditable();
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
