import { expect, test } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { Page as PlaywrightPage } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const TABLE_NAME = `e2e_records_${UNIQUE}`.replace(/[^a-z0-9_]/g, "_");
const INITIAL_DESCRIPTION = "E2E table records acceptance";
const UPDATED_DESCRIPTION = "E2E table records acceptance updated";
const INITIAL_DOCUMENT = {
	name: `Ada ${UNIQUE}`,
	status: "draft",
	count: 1,
};
const UPDATED_DOCUMENT = {
	name: `Ada ${UNIQUE}`,
	status: "published",
	count: 2,
};

let tableId: string | undefined;

async function cleanupTable(api: AuthedApi) {
	if (!tableId) return;
	const response = await api.delete(`/api/tables/${tableId}`);
	expect(
		[200, 204, 404],
		`delete table fixture ${tableId}: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
	tableId = undefined;
}

async function gotoTables(page: PlaywrightPage) {
	await page.goto("/tables");
	await expect(
		page.getByRole("heading", { name: "Data Tables", exact: true }),
	).toBeVisible({ timeout: 15000 });
}

async function searchTable(page: PlaywrightPage) {
	await page.getByRole("textbox", { name: "Search tables" }).fill(TABLE_NAME);
	await expect(page.getByText(TABLE_NAME, { exact: true })).toBeVisible({
		timeout: 10000,
	});
}

async function fillDocumentEditor(
	page: PlaywrightPage,
	value: Record<string, unknown>,
) {
	const dialog = page.getByRole("region", {
		name: /create document|edit document/i,
	});
	await expect(dialog).toBeVisible({ timeout: 10000 });
	const editor = dialog.getByRole("textbox", {
		name: "Document data (JSON)",
		exact: true,
	});
	await editor.focus();
	await page
		.context()
		.grantPermissions(["clipboard-read", "clipboard-write"]);
	await page.evaluate(
		async (text) => navigator.clipboard.writeText(text),
		JSON.stringify(value, null, 2),
	);
	await page.keyboard.press("ControlOrMeta+A");
	await page.keyboard.press("ControlOrMeta+V");
	await expect(dialog.getByRole("alert")).toHaveCount(0);
}

async function expectDocumentStatus(page: PlaywrightPage, status: string) {
	const documents = page.getByRole("region", { name: "Documents" });
	await expect(
		documents.getByRole("cell", {
			name: INITIAL_DOCUMENT.name,
			exact: true,
		}),
	).toBeVisible({
		timeout: 10000,
	});
	await expect(
		documents.getByRole("cell", { name: status, exact: true }),
	).toBeVisible({
		timeout: 10000,
	});
}

test.describe("Table records acceptance", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test.afterEach(async ({ api }) => {
		await cleanupTable(api);
	});

	test("admin creates edits and deletes a table with persisted metadata", async ({
		page,
	}) => {
		await gotoTables(page);

		await page.getByRole("button", { name: "New Table" }).click();
		const createDialog = page.getByRole("region", { name: "Create Table" });
		await expect(createDialog).toBeVisible({ timeout: 10000 });
		await createDialog.getByLabel("Table Name").fill(TABLE_NAME);
		await createDialog
			.getByLabel("Description (Optional)")
			.fill(INITIAL_DESCRIPTION);
		const createTableResponse = page.waitForResponse((response) => {
			const url = new URL(response.url());
			return (
				response.request().method() === "POST" &&
				url.pathname === "/api/tables"
			);
		});
		await createDialog.getByRole("button", { name: "Create" }).click();
		const tableResponse = await createTableResponse;
		if (!tableResponse.ok()) {
			expect(
				tableResponse.ok(),
				`create table via UI: ${tableResponse.status()} ${await tableResponse.text()}`,
			).toBe(true);
		}
		const createdTable = (await tableResponse.json()) as { id: string };
		tableId = createdTable.id;
		await expect(createDialog).toBeHidden({ timeout: 10000 });

		await page.reload();
		await searchTable(page);
		await expect(page.getByText(INITIAL_DESCRIPTION)).toBeVisible();

		await page
			.getByRole("button", { name: `${TABLE_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Edit" }).click();
		const editDialog = page.getByRole("region", { name: "Edit Table" });
		await expect(editDialog).toBeVisible({ timeout: 10000 });
		await editDialog.getByLabel("Description (Optional)").clear();
		await editDialog
			.getByLabel("Description (Optional)")
			.fill(UPDATED_DESCRIPTION);
		await editDialog.getByRole("button", { name: "Update" }).click();
		await expect(editDialog).toBeHidden({ timeout: 10000 });

		await page.reload();
		await searchTable(page);
		await expect(page.getByText(UPDATED_DESCRIPTION)).toBeVisible();

		await page
			.getByRole("button", { name: `${TABLE_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Delete" }).click();
		await page
			.getByRole("alertdialog", { name: "Delete table" })
			.getByRole("button", { name: "Delete table" })
			.click();
		await expect(
			page.getByRole("alertdialog", { name: "Delete table" }),
		).toBeHidden({ timeout: 10000 });
		tableId = undefined;

		await page.reload();
		await page
			.getByRole("textbox", { name: "Search tables" })
			.fill(TABLE_NAME);
		await expect(page.getByText("No tables match your search")).toBeVisible(
			{
				timeout: 10000,
			},
		);
	});

	test("admin creates edits and deletes documents with persisted content", async ({
		page,
		api,
	}) => {
		const response = await api.post("/api/tables", {
			data: { name: TABLE_NAME, description: INITIAL_DESCRIPTION },
		});
		expect(
			response.ok(),
			`create document table fixture: ${await response.text()}`,
		).toBe(true);
		tableId = ((await response.json()) as { id: string }).id;
		await page.goto(`/tables/${tableId}`);
		await expect(
			page.getByRole("heading", { name: TABLE_NAME, exact: true }),
		).toBeVisible();

		const pageContent = page.getByRole("region", {
			name: "Documents",
			exact: true,
		});
		await expect(pageContent.getByText("No documents yet")).toBeVisible({
			timeout: 10000,
		});
		await pageContent.getByRole("button", { name: "Add document" }).click();
		await fillDocumentEditor(page, INITIAL_DOCUMENT);
		await page
			.getByRole("region", { name: "Create Document" })
			.getByRole("button", { name: "Create" })
			.click();
		await expect(
			page.getByRole("region", { name: "Create Document" }),
		).toBeHidden({ timeout: 10000 });

		await page.reload();
		await expectDocumentStatus(page, "draft");

		const documentRow = page.getByRole("row").filter({
			has: page.getByText(INITIAL_DOCUMENT.name, { exact: true }),
		});
		await documentRow.click();
		const inspector = page.getByRole("region", {
			name: "Document inspector",
			exact: true,
		});
		await expect(inspector).toBeVisible();
		await expect(
			inspector.getByRole("tab", { name: "Data", exact: true }),
		).toBeVisible();
		await inspector.getByRole("tab", { name: "JSON", exact: true }).click();
		await expect(inspector.getByLabel("Document JSON")).toContainText(
			'"status": "draft"',
		);
		await inspector
			.getByRole("button", { name: "Close document inspector" })
			.click();
		await expect(inspector).toBeHidden();

		await documentRow
			.getByRole("button", { name: /^Document .* actions$/ })
			.click();
		await page.getByRole("menuitem", { name: "Edit" }).click();
		await fillDocumentEditor(page, UPDATED_DOCUMENT);
		await page
			.getByRole("region", { name: "Edit Document" })
			.getByRole("button", { name: "Update" })
			.click();
		await expect(
			page.getByRole("region", { name: "Edit Document" }),
		).toBeHidden({ timeout: 10000 });

		await page.reload();
		await expectDocumentStatus(page, "published");

		const updatedDocumentRow = page.getByRole("row").filter({
			has: page.getByText(INITIAL_DOCUMENT.name, { exact: true }),
		});
		await updatedDocumentRow
			.getByRole("button", { name: /^Document .* actions$/ })
			.click();
		await page.getByRole("menuitem", { name: "Delete" }).click();
		await page
			.getByRole("alertdialog", { name: /delete document/i })
			.getByRole("button", { name: "Delete document" })
			.click();
		await expect(
			page.getByRole("alertdialog", { name: /delete document/i }),
		).toBeHidden({ timeout: 10000 });

		await page.reload();
		await expect(
			page
				.getByRole("region", { name: "Documents", exact: true })
				.getByText("No documents yet"),
		).toBeVisible({ timeout: 10000 });
	});
});
