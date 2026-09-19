/**
 * Organization Management Tests (Admin)
 *
 * Tests organization CRUD operations from the platform admin perspective.
 * These tests run as platform_admin with full system access.
 *
 * Mirrors: api/tests/e2e/api/test_organizations.py
 */

import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures/api-fixture";

function organizationRow(page: Page, name: string) {
	return page.getByRole("row", { name: new RegExp(name) });
}

test.describe("Organization Management", () => {
	test("displays the organization list with standard row actions", async ({
		page,
	}) => {
		await page.goto("/organizations");

		// Should see organizations page
		await expect(
			page.getByRole("heading", { name: /organizations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		const organizationRow = page.locator("table tbody tr").first();
		await expect(organizationRow).toBeVisible();

		await organizationRow.getByRole("button", { name: /actions$/ }).click();
		await expect(
			page.getByRole("menuitem", { name: /^Edit / }),
		).toBeVisible();
		await expect(
			page.getByRole("menuitem", { name: /^Disable / }),
		).toBeVisible();
	});

	test("edits status and instructions from the organization row", async ({
		page,
		api,
	}) => {
		const organizationName = `Organization UX ${Date.now()}`;
		const createResponse = await api.post("/api/organizations", {
			data: {
				name: organizationName,
				domain: `organization-ux-${Date.now()}.example`,
			},
		});
		expect(createResponse.ok(), await createResponse.text()).toBeTruthy();
		const organization = (await createResponse.json()) as { id: string };

		try {
			await page.goto("/organizations");
			await page
				.getByRole("textbox", { name: "Search organizations" })
				.fill(organizationName);
			const organizationRow = page.getByRole("row", {
				name: new RegExp(organizationName),
			});
			await expect(organizationRow).toBeVisible({ timeout: 10000 });

			await organizationRow.click();
			await expect(
				page.getByRole("heading", { name: "Edit Organization" }),
			).toBeVisible();
			await page
				.getByRole("switch", { name: "Organization Status" })
				.click();
			await page.getByRole("button", { name: "Save Changes" }).click();
			await expect(
				page.getByRole("heading", { name: "Edit Organization" }),
			).toBeHidden();
			await expect(organizationRow).toBeHidden();

			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(organizationRow).toBeVisible();
			await organizationRow.click();
			await expect(
				page.getByRole("switch", { name: "Organization Status" }),
			).not.toBeChecked();

			await page.getByRole("tab", { name: "Instructions" }).click();
			await expect(
				page.getByRole("heading", {
					name: "Organization Instructions",
				}),
			).toBeVisible();
		} finally {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/organizations/${organization.id}`)
				).status(),
			);
		}
	});

	test("[ORG-01 desktop] creates, edits, reloads, and disables an organization", async ({
		page,
		api,
	}) => {
		const unique = Date.now();
		const originalName = `Organization UI ${unique}`;
		const originalDomain = `org-ui-${unique}.example`;
		const editedName = `Organization UI Edited ${unique}`;
		const editedDomain = `org-ui-edited-${unique}.example`;
		let organizationId: string | undefined;

		await page.goto("/organizations");
		await expect(
			page.getByRole("heading", { name: /organizations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		try {
			await page
				.getByRole("button", { name: "New Organization" })
				.click();
			const createDialog = page.getByRole("dialog", {
				name: "Create Organization",
			});
			await expect(createDialog).toBeVisible();
			await createDialog
				.getByLabel("Organization Name")
				.fill(originalName);
			await createDialog.getByLabel("Email Domain").fill(originalDomain);
			await createDialog
				.getByRole("button", { name: "Create Organization" })
				.click();
			await expect(
				page.getByRole("dialog", { name: "Create Organization" }),
			).toBeHidden({ timeout: 10000 });

			await page
				.getByRole("textbox", { name: "Search organizations" })
				.fill(originalName);
			const createdRow = organizationRow(page, originalName);
			await expect(createdRow).toBeVisible({ timeout: 10000 });
			await expect(createdRow).toContainText(originalDomain);
			await expect(createdRow.getByText("Active")).toBeVisible();
			organizationId = await createdRow.getAttribute("data-org-id");
			expect(organizationId).toBeTruthy();

			await createdRow
				.getByRole("button", { name: `Edit ${originalName}` })
				.click();
			const editDialog = page.getByRole("dialog", {
				name: "Edit Organization",
			});
			await expect(editDialog).toBeVisible();
			await editDialog.getByLabel("Organization Name").fill(editedName);
			await editDialog.getByLabel("Email Domain").fill(editedDomain);
			await editDialog
				.getByRole("button", { name: "Save Changes" })
				.click();
			await expect(editDialog).toBeHidden({ timeout: 10000 });

			await page.reload();
			await expect(
				page.getByRole("heading", { name: /organizations/i }).first(),
			).toBeVisible({ timeout: 10000 });
			await page
				.getByRole("textbox", { name: "Search organizations" })
				.fill(editedName);
			const editedRow = organizationRow(page, editedName);
			await expect(editedRow).toBeVisible({ timeout: 10000 });
			await expect(editedRow).toContainText(editedDomain);
			await expect(organizationRow(page, originalName)).toBeHidden();

			await editedRow
				.getByRole("button", { name: `${editedName} actions` })
				.click();
			await page
				.getByRole("menuitem", { name: `Disable ${editedName}` })
				.click();
			await expect(
				page.getByRole("alertdialog", {
					name: "Disable organization?",
				}),
			).toBeVisible();
			await page.getByRole("button", { name: "Disable" }).click();
			await expect(editedRow).toBeHidden({ timeout: 10000 });

			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(editedRow).toBeVisible({ timeout: 10000 });
			await expect(editedRow.getByText("Inactive")).toBeVisible();

			await page.reload();
			await expect(
				page.getByRole("heading", { name: /organizations/i }).first(),
			).toBeVisible({ timeout: 10000 });
			await page
				.getByRole("textbox", { name: "Search organizations" })
				.fill(editedName);
			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(
				organizationRow(page, editedName).getByText("Inactive", {
					exact: true,
				}),
			).toBeVisible();
		} finally {
			if (organizationId) {
				expect([200, 204, 404]).toContain(
					(
						await api.delete(`/api/organizations/${organizationId}`)
					).status(),
				);
			}
		}
	});
});

test.describe("Organization Settings", () => {
	test("opens organization instructions from the edit dialog", async ({
		page,
		api,
	}) => {
		const organizationName = `Organization Settings ${Date.now()}`;
		const createResponse = await api.post("/api/organizations", {
			data: {
				name: organizationName,
				domain: `organization-settings-${Date.now()}.example`,
			},
		});
		expect(createResponse.ok(), await createResponse.text()).toBeTruthy();
		const organization = (await createResponse.json()) as { id: string };

		try {
			await page.goto("/organizations");
			const row = organizationRow(page, organizationName);
			await expect(row).toBeVisible({ timeout: 10000 });
			await row.click();
			await page.getByRole("tab", { name: "Instructions" }).click();
			await expect(
				page.getByRole("heading", {
					name: "Organization Instructions",
				}),
			).toBeVisible();
		} finally {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/organizations/${organization.id}`)
				).status(),
			);
		}
	});
});
