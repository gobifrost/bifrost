import type { Page } from "./fixtures/api-fixture";

import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const ROLE_NAME = `ROLE-01 Lifecycle ${SUFFIX}`;
const UPDATED_ROLE_NAME = `ROLE-01 Lifecycle Updated ${SUFFIX}`;
const ROLE_DESCRIPTION = "Created through the role lifecycle acceptance test";
const UPDATED_ROLE_DESCRIPTION =
	"Updated through the role lifecycle acceptance test";

type RoleSummary = {
	id: string;
	name: string;
	description?: string | null;
};

async function deleteMatchingRoles(api: AuthedApi, names: string[]) {
	for (const name of names) {
		const response = await api.get("/api/roles", {
			params: { search: name, limit: 25, offset: 0 },
		});
		expect(response.ok(), await response.text()).toBe(true);
		const body = (await response.json()) as
			RoleSummary[] | { items?: RoleSummary[] };
		const roles = Array.isArray(body) ? body : (body.items ?? []);
		for (const role of roles) {
			if (role.name !== name) continue;
			const deleteResponse = await api.delete(`/api/roles/${role.id}`);
			expect(deleteResponse.ok(), await deleteResponse.text()).toBe(true);
		}
	}
}

async function searchRoles(page: Page, query: string) {
	await page.goto("/roles");
	await page
		.getByPlaceholder(/search roles by name or description/i)
		.fill(query);
}

function roleRow(page: Page, name: string) {
	return page.getByRole("row", { name: new RegExp(name) });
}

async function openRoleActions(page: Page, name: string) {
	await roleRow(page, name)
		.getByRole("button", { name: `${name} actions` })
		.click();
}

test.describe("Role lifecycle acceptance", () => {
	test.beforeAll(async ({ api }) => {
		await deleteMatchingRoles(api, [ROLE_NAME, UPDATED_ROLE_NAME]);
	});

	test.afterAll(async ({ api }) => {
		await deleteMatchingRoles(api, [ROLE_NAME, UPDATED_ROLE_NAME]);
	});

	test("ROLE-01 creates, edits, reloads, and deletes a role", async ({
		page,
	}) => {
		await page.goto("/roles");
		await page.getByRole("button", { name: /create role/i }).first().click();

		const dialog = page.getByRole("dialog", { name: /create role/i });
		await expect(dialog).toBeVisible();
		await dialog.getByLabel(/role name/i).fill(ROLE_NAME);
		await dialog.getByLabel(/description/i).fill(ROLE_DESCRIPTION);
		await dialog.getByRole("button", { name: "Create" }).click();
		await expect(dialog).toBeHidden();

		await searchRoles(page, ROLE_NAME);
		await expect(roleRow(page, ROLE_NAME)).toContainText(ROLE_DESCRIPTION);

		await page.reload();
		await page
			.getByPlaceholder(/search roles by name or description/i)
			.fill(ROLE_NAME);
		await expect(roleRow(page, ROLE_NAME)).toContainText(ROLE_DESCRIPTION);

		await openRoleActions(page, ROLE_NAME);
		await page.getByRole("menuitem", { name: "Edit" }).click();

		const editDialog = page.getByRole("dialog", { name: /edit role/i });
		await expect(editDialog).toBeVisible();
		await editDialog.getByLabel(/role name/i).fill(UPDATED_ROLE_NAME);
		await editDialog
			.getByLabel(/description/i)
			.fill(UPDATED_ROLE_DESCRIPTION);
		await editDialog.getByRole("button", { name: "Update" }).click();
		await expect(editDialog).toBeHidden();

		await searchRoles(page, UPDATED_ROLE_NAME);
		await expect(roleRow(page, UPDATED_ROLE_NAME)).toContainText(
			UPDATED_ROLE_DESCRIPTION,
		);

		await page.reload();
		await page
			.getByPlaceholder(/search roles by name or description/i)
			.fill(UPDATED_ROLE_NAME);
		await expect(roleRow(page, UPDATED_ROLE_NAME)).toContainText(
			UPDATED_ROLE_DESCRIPTION,
		);

		await openRoleActions(page, UPDATED_ROLE_NAME);
		await page.getByRole("menuitem", { name: "Delete" }).click();
		const deleteDialog = page.getByRole("alertdialog", {
			name: /delete role/i,
		});
		await expect(deleteDialog).toContainText(UPDATED_ROLE_NAME);
		await deleteDialog.getByRole("button", { name: "Delete role" }).click();
		await expect(deleteDialog).toBeHidden();

		await expect(roleRow(page, UPDATED_ROLE_NAME)).toHaveCount(0);
		await page.reload();
		await page
			.getByPlaceholder(/search roles by name or description/i)
			.fill(UPDATED_ROLE_NAME);
		await expect(
			page.getByText(/no roles match your search/i),
		).toBeVisible();
	});
});
