/**
 * Policy Rules — reference mode (Admin)
 *
 * Happy-path for inserting a `{"$ref": name}` named-rule reference into
 * the Files and Tables policy editors:
 *
 *   Files:
 *     1. Create a named policy rule in the "file" domain via the API.
 *     2. Open the Files explorer, create a share, open the policy editor.
 *     3. Confirm the "Insert reference…" dropdown is visible and lists the rule.
 *     4. Pick the rule — the editor's doc gains a `$ref` entry.
 *
 *   Tables:
 *     1. Create a named policy rule in the "table" domain via the API.
 *     2. Create a table, open its policy editor.
 *     3. Confirm the "Insert reference…" dropdown is visible and lists the rule.
 *     4. Pick the rule — the editor's doc gains a `$ref` entry.
 *
 * NOTE: The "save" step is intentionally omitted — saving a doc that contains
 * an unresolvable `$ref` (the named rule's body is empty here) returns a 422.
 * The test only exercises the UI affordance (rule appears in list, inserting
 * adds it to the buffer), which is what the component-level vitest tests can't
 * cover end-to-end.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";
import type { AuthedApi } from "./fixtures/api-fixture";
import { routeMonacoAssets } from "./fixtures/monaco-assets";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const FILE_RULE_NAME = `e2e-file-ref-rule-${UNIQUE}`;
const TABLE_RULE_NAME = `e2e-table-ref-rule-${UNIQUE}`;
const SHARE_NAME = `e2e-ref-share-${UNIQUE}`.replace(/[^a-z0-9-]/g, "-");
const TABLE_NAME = `e2e_ref_table_${UNIQUE}`.replace(/[^a-z0-9_]/g, "_");
let tableId: string | undefined;

async function expectCleanupStatus(
	api: AuthedApi,
	label: string,
	cleanup: () => Promise<Awaited<ReturnType<AuthedApi["delete"]>>>,
) {
	const response = await cleanup();
	expect(
		[200, 204, 404],
		`${label}: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
}

async function policyEditorText(page: Page, modelPath: string) {
	return page.evaluate((targetPath) => {
		const monaco = (
			window as typeof window & {
				monaco?: {
					editor?: {
						getModels?: () => Array<{
							uri: { path: string };
							getValue: () => string;
						}>;
					};
				};
			}
		).monaco;
		const model = monaco?.editor
			?.getModels?.()
			.find((item) => item.uri.path.endsWith(targetPath));
		if (!model) throw new Error(`${targetPath} Monaco model not found`);
		return model.getValue();
	}, modelPath);
}

test.describe("Policy rule reference mode", () => {
	test.beforeEach(async ({ page }) => {
		await routeMonacoAssets(page.context());
	});

	test.beforeAll(async ({ api }) => {
		// Create a named file policy rule.
		const fileRuleRes = await api.post("/api/policy-rules", {
			data: {
				name: FILE_RULE_NAME,
				domain: "file",
				description: "E2E test file rule",
				body: { actions: ["read"], when: null },
			},
		});
		expect(
			fileRuleRes.ok(),
			`create file rule: ${await fileRuleRes.text()}`,
		).toBe(true);

		// Create a named table policy rule.
		const tableRuleRes = await api.post("/api/policy-rules", {
			data: {
				name: TABLE_RULE_NAME,
				domain: "table",
				description: "E2E test table rule",
				body: { actions: ["read"], when: null },
			},
		});
		expect(
			tableRuleRes.ok(),
			`create table rule: ${await tableRuleRes.text()}`,
		).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		await expectCleanupStatus(
			api,
			`delete share policy ${SHARE_NAME}`,
			() =>
				api.delete("/api/files/policies/", {
					params: { location: SHARE_NAME },
				}),
		);
		if (tableId) {
			await expectCleanupStatus(
				api,
				`delete table fixture ${tableId}`,
				() => api.delete(`/api/tables/${tableId}`),
			);
		}
		await expectCleanupStatus(
			api,
			`delete file rule ${FILE_RULE_NAME}`,
			() => api.delete(`/api/policy-rules/file/${FILE_RULE_NAME}`),
		);
		await expectCleanupStatus(
			api,
			`delete table rule ${TABLE_RULE_NAME}`,
			() => api.delete(`/api/policy-rules/table/${TABLE_RULE_NAME}`),
		);
	});

	test("Files policy editor adds a shared rule through the Access pane", async ({
		page,
	}) => {
		// Navigate to the Files explorer.
		await page.goto("/files");
		await expect(
			page.getByRole("heading", { name: /files/i }).first(),
		).toBeVisible({ timeout: 15000 });

		// Create a new share via the UI.
		await page.getByRole("button", { name: /new share/i }).click();
		await page.getByLabel(/share name/i).fill(SHARE_NAME);
		await page.getByRole("button", { name: /create share/i }).click();

		// Select the share and open the root policy editor.
		await expect(
			page.getByText(SHARE_NAME, { exact: false }).first(),
		).toBeVisible({ timeout: 10000 });
		await page.getByRole("tab", { name: "Policies" }).click();
		await page
			.getByRole("button", { name: `Manage policy for ${SHARE_NAME}/` })
			.click();

		const policyPane = page.getByRole("region", { name: "File details" });
		await expect(
			policyPane.getByRole("heading", {
				name: "Manage Policy",
				exact: true,
			}),
		).toBeVisible();
		await policyPane
			.getByRole("combobox", { name: "Add Shared Rule" })
			.click();
		await page
			.getByRole("option", {
				name: new RegExp(FILE_RULE_NAME.replaceAll("-", " "), "i"),
			})
			.click();
		await expect(
			policyPane.getByRole("list", { name: "Policy rules" }),
		).toContainText(new RegExp(FILE_RULE_NAME.replaceAll("-", " "), "i"));
	});

	test("Tables policy editor shows the rule in Insert reference dropdown", async ({
		page,
		api,
	}) => {
		// Create a table.
		const tableRes = await api.post("/api/tables", {
			data: {
				name: TABLE_NAME,
				schema: { properties: {}, additionalProperties: true },
			},
		});
		expect(tableRes.ok(), `create table: ${await tableRes.text()}`).toBe(
			true,
		);
		const tableData = (await tableRes.json()) as { id: string };
		tableId = tableData.id;

		// Navigate to Tables and open the table edit dialog.
		await page.goto("/tables");
		await expect(
			page.getByRole("heading", { name: /tables/i }).first(),
		).toBeVisible({ timeout: 15000 });

		await expect(page.getByText(TABLE_NAME)).toBeVisible({
			timeout: 10000,
		});
		await page
			.getByRole("button", { name: `${TABLE_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Edit" }).click();
		const tableDialog = page.getByRole("region", {
			name: "Edit Table",
			exact: true,
		});
		await expect(tableDialog).toBeVisible({ timeout: 10000 });

		// The "Insert reference…" dropdown should appear with the table rule.
		const refTrigger = tableDialog.getByLabel(/insert reference/i);
		await expect(refTrigger).toBeVisible({ timeout: 10000 });
		await refTrigger.click();
		await expect(
			page.getByRole("option", { name: TABLE_RULE_NAME }),
		).toBeVisible({ timeout: 5000 });
		await page.getByRole("option", { name: TABLE_RULE_NAME }).click();
		await expect
			.poll(() => policyEditorText(page, "policies.json"))
			.toContain(TABLE_RULE_NAME);

		// Cleanup.
		const tableCleanup = await api.delete(`/api/tables/${tableData.id}`);
		expect(
			[200, 204, 404],
			`delete table fixture ${tableData.id}: ${tableCleanup.status()} ${await tableCleanup.text()}`,
		).toContain(tableCleanup.status());
		tableId = undefined;
	});
});
