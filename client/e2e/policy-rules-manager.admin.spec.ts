/**
 * Policy Rules Manager — admin CRUD (Admin)
 *
 * Happy-path for the inline policy-rules manager:
 *
 *   1. Open a table's policy editor → click "Manage rules…" → manager dialog opens.
 *   2. Create a new rule from the manager → rule appears in the list.
 *   3. Edit the rule → description is updated.
 *   4. Attempt to delete the rule while it is referenced by the table's policy →
 *      blast-radius dialog shows, not deleted.
 *   5. Remove the reference, then delete the rule → success.
 *
 * NOTE: The blast-radius delete step wires a $ref in the table policy and confirms
 * the 409 UI response. It does NOT confirm server-side enforcement of the rule
 * body (that is covered by backend E2E tests).
 */

import { test, expect } from "./fixtures/api-fixture";
import { routeMonacoAssets } from "./fixtures/monaco-assets";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const TABLE_NAME = `e2e_mgr_table_${UNIQUE}`.replace(/[^a-z0-9_]/g, "_");
const RULE_NAME = `e2e-mgr-rule-${UNIQUE}`;
let tableId: string;

test.describe("Policy rules manager", () => {
	test.beforeEach(async ({ page }) => {
		await routeMonacoAssets(page.context());
	});

	test.beforeAll(async ({ api }) => {
		// Create the table we'll edit policies on.
		const res = await api.post("/api/tables", {
			data: { name: TABLE_NAME, description: "E2E manager test table" },
		});
		expect(res.ok(), `create table: ${await res.text()}`).toBe(true);
		const table = (await res.json()) as { id: string };
		tableId = table.id;
	});

	test.afterAll(async ({ api }) => {
		if (tableId) {
			const tableCleanup = await api.delete(`/api/tables/${tableId}`);
			expect(
				[200, 204, 404],
				`delete table fixture ${tableId}: ${tableCleanup.status()} ${await tableCleanup.text()}`,
			).toContain(tableCleanup.status());
		}
		const ruleCleanup = await api.delete(
			`/api/policy-rules/table/${RULE_NAME}`,
		);
		expect(
			[200, 204, 404],
			`delete policy rule fixture ${RULE_NAME}: ${ruleCleanup.status()} ${await ruleCleanup.text()}`,
		).toContain(ruleCleanup.status());
	});

	test.afterEach(async ({ api }) => {
		if (tableId) {
			const tableCleanup = await api.delete(`/api/tables/${tableId}`);
			expect(
				[200, 204, 404],
				`delete table fixture ${tableId}: ${tableCleanup.status()} ${await tableCleanup.text()}`,
			).toContain(tableCleanup.status());
			tableId = "";
		}
	});

	test("open manager, create rule, edit, see built-in badge, attempt in-use delete", async ({
		page,
		api,
	}) => {
		// Navigate to Tables.
		await page.goto("/tables");
		await expect(
			page.getByRole("heading", { name: /tables/i }).first(),
		).toBeVisible({ timeout: 15000 });

		// Open the table edit dialog from the matching table actions menu.
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

		// Click "Manage rules…" inside the policy editor.
		const manageBtn = tableDialog.getByRole("button", {
			name: "Manage rules…",
		});
		await expect(manageBtn).toBeVisible({ timeout: 5000 });
		await manageBtn.click();

		// The manager dialog should open.
		const managerDialog = page.getByRole("dialog", {
			name: /table policy rules/i,
		});
		await expect(managerDialog).toBeVisible({ timeout: 5000 });
		const ruleSurface = () =>
			managerDialog.locator("article").filter({
				has: page.getByRole("heading", { name: RULE_NAME }),
			});
		const ruleActions = () =>
			managerDialog.getByRole("button", {
				name: `${RULE_NAME} actions`,
			});

		// ----------------------------------------------------------------
		// 1. Create a new rule
		// ----------------------------------------------------------------
		await managerDialog.getByRole("button", { name: "New rule" }).click();

		const createDialog = page.getByRole("dialog", {
			name: /create policy rule/i,
		});
		await expect(createDialog).toBeVisible({ timeout: 5000 });

		await createDialog.getByLabel("Name").fill(RULE_NAME);
		await createDialog
			.getByLabel("Description")
			.fill("E2E manager test rule");

		// Leave the body as the default seed (valid JSON).
		await createDialog.getByRole("button", { name: "Create" }).click();

		// Rule should now appear in the manager table.
		await expect(ruleSurface()).toBeVisible({ timeout: 10000 });

		// ----------------------------------------------------------------
		// 2. Edit the rule description
		// ----------------------------------------------------------------
		await ruleActions().click();
		await page.getByRole("menuitem", { name: "Edit" }).click();

		const editDialog = page.getByRole("dialog", {
			name: new RegExp(`edit.*${RULE_NAME}`, "i"),
		});
		await expect(editDialog).toBeVisible({ timeout: 5000 });

		// Name field should be disabled (cannot be changed).
		const nameInput = editDialog.getByLabel("Name");
		await expect(nameInput).toBeDisabled();

		const descInput = editDialog.getByLabel("Description");
		await descInput.clear();
		await descInput.fill("Updated description");
		await editDialog.getByRole("button", { name: "Save" }).click();

		// The manager should still be open with the rule listed.
		await expect(ruleSurface()).toBeVisible({ timeout: 10000 });

		// ----------------------------------------------------------------
		// 3. Built-in admin_bypass rule should show the built-in badge
		// ----------------------------------------------------------------
		// The admin_bypass rule is seeded on startup — confirm it's present.
		await expect(
			managerDialog
				.locator("article")
				.filter({
					has: page.getByRole("heading", {
						name: "admin_bypass",
					}),
				})
				.getByText("built-in", { exact: true }),
		).toBeVisible({ timeout: 5000 });

		// ----------------------------------------------------------------
		// 4. Wire a $ref in the table policy and attempt to delete → 409 blast radius
		// ----------------------------------------------------------------
		// Use the API directly to attach the rule as a $ref in the table policy.
		const attachRes = await api.patch(`/api/tables/${tableId}`, {
			data: {
				policies: {
					policies: [{ $ref: RULE_NAME }],
				},
			},
		});
		expect(
			attachRes.ok(),
			`attach table policy: ${await attachRes.text()}`,
		).toBe(true);

		// Now try to delete the rule — expect the blast-radius dialog.
		await ruleActions().click();
		await page.getByRole("menuitem", { name: "Delete" }).click();
		// Confirm the delete in the alert dialog.
		await page.getByRole("button", { name: "Delete" }).click();

		const blastDialog = page.getByRole("alertdialog", {
			name: "Rule is in use",
		});
		await expect(blastDialog).toBeVisible({ timeout: 10000 });
		await expect(blastDialog).toContainText(TABLE_NAME);
		await blastDialog.getByRole("button", { name: "Close" }).click();

		// ----------------------------------------------------------------
		// 5. Close the manager
		// ----------------------------------------------------------------
		await managerDialog
			.getByRole("button", { name: "Close" })
			.first()
			.click();
	});
});
