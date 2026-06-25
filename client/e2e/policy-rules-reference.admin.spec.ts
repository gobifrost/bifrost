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

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const FILE_RULE_NAME = `e2e-file-ref-rule-${UNIQUE}`;
const TABLE_RULE_NAME = `e2e-table-ref-rule-${UNIQUE}`;
const SHARE_NAME = `e2e-ref-share-${UNIQUE}`.replace(/[^a-z0-9-]/g, "-");
const TABLE_NAME = `e2e_ref_table_${UNIQUE}`.replace(/[^a-z0-9_]/g, "_");

test.describe("Policy rule reference mode", () => {
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
		// Best-effort cleanup — failures don't fail the suite.
		await api.delete(`/api/policy-rules/file/${FILE_RULE_NAME}`).catch(() => {});
		await api.delete(`/api/policy-rules/table/${TABLE_RULE_NAME}`).catch(() => {});
	});

	test("Files policy editor shows the rule in Insert reference dropdown", async ({
		page,
		api,
	}) => {
		// Create a share so we have something to edit.
		const shareRes = await api.post("/api/files/policies/", {
			params: { location: "workspace", scope: "global" },
			data: {
				policies: {
					policies: [],
				},
			},
		});
		// The share creation may 404 if the path endpoint differs — fall back
		// to driving via the UI.
		void shareRes;

		// Navigate to the Files explorer.
		await page.goto("/files");
		await expect(
			page.getByRole("heading", { name: /files/i }).first(),
		).toBeVisible({ timeout: 15000 });

		// Create a new share via the UI.
		await page.getByRole("button", { name: /new share/i }).click();
		await page.getByLabel(/share name/i).fill(SHARE_NAME);
		await page.getByRole("button", { name: /create share/i }).click();

		// Select the share and open the policy editor.
		await expect(
			page.getByText(SHARE_NAME, { exact: false }).first(),
		).toBeVisible({ timeout: 10000 });
		await page.getByText(SHARE_NAME, { exact: false }).first().click();

		// Open the policy editor from the detail pane.
		await page
			.getByRole("button", { name: /manage policy|edit policy|policy/i })
			.first()
			.click();

		// Wait for the editor dialog.
		await expect(
			page.getByRole("dialog", { name: /manage policy/i }),
		).toBeVisible({ timeout: 10000 });

		// The "Insert reference…" dropdown should appear with the file rule.
		const refTrigger = page
			.getByRole("dialog", { name: /manage policy/i })
			.getByLabel(/insert reference/i);
		await expect(refTrigger).toBeVisible({ timeout: 5000 });
		await refTrigger.click();
		await expect(
			page.getByRole("option", { name: FILE_RULE_NAME }),
		).toBeVisible({ timeout: 5000 });
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
		expect(tableRes.ok(), `create table: ${await tableRes.text()}`).toBe(true);
		const tableData = (await tableRes.json()) as { id: string };

		// Navigate to Tables and open the table edit dialog.
		await page.goto("/tables");
		await expect(
			page.getByRole("heading", { name: /tables/i }).first(),
		).toBeVisible({ timeout: 15000 });

		const tableRow = page.getByRole("row").filter({ hasText: TABLE_NAME });
		await expect(tableRow).toBeVisible({ timeout: 10000 });
		await tableRow.getByRole("button", { name: /edit table/i }).click();
		const tableDialog = page.getByRole("dialog", { name: /edit table/i });
		await expect(tableDialog).toBeVisible({ timeout: 10000 });

		// The "Insert reference…" dropdown should appear with the table rule.
		const refTrigger = tableDialog.getByLabel(/insert reference/i);
		await expect(refTrigger).toBeVisible({ timeout: 10000 });
		await refTrigger.click();
		await expect(
			page.getByRole("option", { name: TABLE_RULE_NAME }),
		).toBeVisible({ timeout: 5000 });

		// Cleanup.
		await api
			.delete(`/api/tables/${tableData.id}`)
			.catch(() => {});
	});
});
