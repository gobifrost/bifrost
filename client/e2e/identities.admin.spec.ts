/**
 * Identities tab — admin happy path.
 *
 * A Platform Admin opens Users → Identities: the global Default Identity is
 * pinned first. They create a custom identity in an organization, find its
 * row, open it, rename it on its Profile tab, and delete it.
 */

import { randomUUID } from "node:crypto";

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = randomUUID().slice(0, 8);
const ORG_NAME = `Contoso ${SUFFIX}`;
const IDENTITY_NAME = `Fabrikam Sync ${SUFFIX}`;
const RENAMED = `Fabrikam Nightly ${SUFFIX}`;

test.describe("Identities tab", () => {
	let organizationId: string;

	test.beforeAll(async ({ api }) => {
		const org = await api.post("/api/organizations", {
			data: { name: ORG_NAME, domain: `contoso-${SUFFIX}.gobifrost.dev` },
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (!organizationId) return;
		// The journey deletes its identity; this removes it if a step failed.
		const listed = await api.get("/api/identities", {
			params: { organization_id: organizationId },
		});
		expect(listed.ok(), `list identities: ${listed.status()}`).toBe(true);
		const leftovers = (
			(await listed.json()) as {
				id: string;
				identity_kind: string;
			}[]
		).filter((identity) => identity.identity_kind === "custom");
		for (const identity of leftovers) {
			const deleted = await api.delete(`/api/identities/${identity.id}`);
			expect([204, 404]).toContain(deleted.status());
		}
		const deleted = await api.delete(
			`/api/organizations/${organizationId}`,
		);
		expect([200, 204, 404]).toContain(deleted.status());
	});

	test("IDENTITY-01 creates, opens, renames and deletes a custom identity", async ({
		page,
	}) => {
		await page.goto("/users");
		await page
			.getByRole("navigation", { name: "User views" })
			.getByRole("link", { name: "Identities" })
			.click();
		await expect(page).toHaveURL(/\/users\/identities$/);
		await expect(
			page.getByRole("heading", { level: 1, name: "Identities" }),
		).toBeVisible();

		// Global first: its Kind and Organization both say Global.
		const pinned = page.locator("table tbody tr").first();
		await expect(
			pinned.getByRole("cell").nth(0).getByLabel("Organization"),
		).toHaveText("Global");
		await expect(pinned.getByRole("cell").nth(1)).toHaveText(
			"Default Identity",
		);
		await expect(pinned.getByRole("cell").nth(2)).toHaveText("Global");

		await page.getByRole("button", { name: "New Identity" }).click();
		const dialog = page.getByRole("dialog", { name: "New Identity" });
		await dialog.getByRole("textbox", { name: "Name" }).fill(IDENTITY_NAME);
		await dialog.getByRole("combobox", { name: "Organization" }).click();
		await page.getByPlaceholder("Search organizations...").fill(ORG_NAME);
		await page.getByRole("option", { name: ORG_NAME }).click();
		await dialog.getByRole("button", { name: "Create Identity" }).click();
		await expect(dialog).toBeHidden();

		// Creating opens the identity; its row is on the Identities tab.
		await expect(
			page.getByRole("heading", {
				level: 1,
				name: new RegExp(IDENTITY_NAME),
			}),
		).toBeVisible();
		await page
			.getByRole("link", { name: "Identities", exact: true })
			.click();
		await page
			.getByPlaceholder("Search identities by name or organization...")
			.fill(IDENTITY_NAME);
		const row = page.getByRole("row").filter({ hasText: IDENTITY_NAME });
		await expect(
			row.getByRole("cell").nth(0).getByLabel("Organization"),
		).toHaveText(ORG_NAME);
		await expect(row.getByRole("cell").nth(2)).toHaveText("Custom");
		await row.getByRole("link", { name: IDENTITY_NAME }).click();
		await expect(page).toHaveURL(/\/users\/[0-9a-f-]+$/);

		await page.getByRole("tab", { name: "Profile" }).click();
		const name = page.getByRole("textbox", { name: "Name" });
		await expect(name).toHaveValue(IDENTITY_NAME);
		await name.fill(RENAMED);
		await page.getByRole("button", { name: "Save Name" }).click();
		await expect(page.getByText("Identity renamed")).toBeVisible();
		await expect(
			page.getByRole("heading", { level: 1, name: new RegExp(RENAMED) }),
		).toBeVisible();

		await page.getByRole("button", { name: `${RENAMED} actions` }).click();
		await page.getByRole("menuitem", { name: "Delete" }).click();
		await page
			.getByRole("alertdialog", { name: "Delete Identity" })
			.getByRole("button", { name: "Delete Identity" })
			.click();
		await expect(page).toHaveURL(/\/users\/identities$/);
		await page
			.getByPlaceholder("Search identities by name or organization...")
			.fill(RENAMED);
		await expect(page.getByText("No Matching Identities")).toBeVisible();
	});
});
