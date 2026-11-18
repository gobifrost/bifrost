/**
 * Identity Providers — admin happy path.
 *
 * A Platform Admin adds a SCIM connection for an organization and copies its
 * one-time token and Tenant URL. The identity provider pushes a group with
 * that token, and the admin maps the group to a role.
 */

import { randomUUID } from "node:crypto";

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = randomUUID().slice(0, 8);
const ORG_NAME = `Contoso ${SUFFIX}`;
const PROVIDER_NAME = `Contoso Entra ${SUFFIX}`;
const ROLE_NAME = `L1 Techs ${SUFFIX}`;
const GROUP_NAME = `Ops-L1 ${SUFFIX}`;

test.describe("Identity Providers", () => {
	let organizationId: string;
	let roleId: string;

	test.beforeAll(async ({ api }) => {
		const org = await api.post("/api/organizations", {
			data: { name: ORG_NAME },
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;
		const role = await api.post("/api/roles", { data: { name: ROLE_NAME } });
		expect(role.ok(), `create role: ${await role.text()}`).toBe(true);
		roleId = ((await role.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (organizationId) {
			const listed = await api.get("/api/identity-providers", {
				params: { organization_id: organizationId },
			});
			for (const provider of (await listed.json()) as { id: string }[]) {
				await api.delete(`/api/identity-providers/${provider.id}`);
			}
			await api.delete(`/api/organizations/${organizationId}`);
		}
		if (roleId) await api.delete(`/api/roles/${roleId}`);
	});

	test("IDP-01 adds a connection, receives a group and maps it to a role", async ({
		page,
		api,
	}) => {
		await page.goto("/users");
		await page
			.getByRole("navigation", { name: "User views" })
			.getByRole("link", { name: "Identity Providers" })
			.click();
		await expect(page).toHaveURL(/\/users\/identity-providers$/);

		await page.getByRole("button", { name: "Add Provider" }).click();
		const dialog = page.getByRole("dialog", { name: "New Identity Provider" });
		await dialog
			.getByRole("textbox", { name: "Display Name" })
			.fill(PROVIDER_NAME);
		await dialog.getByRole("combobox", { name: "Organization" }).click();
		await page.getByPlaceholder("Search organizations...").fill(ORG_NAME);
		await page.getByRole("option", { name: ORG_NAME }).click();
		await dialog.getByRole("button", { name: "Create Connection" }).click();

		await expect(
			page.getByRole("heading", { level: 1, name: PROVIDER_NAME }),
		).toBeVisible();
		const token = (
			await page.getByLabel("Secret value").textContent()
		)?.trim();
		expect(token).toMatch(/^bfidp_/);
		const tenantUrl = await page
			.getByRole("textbox", { name: "Tenant URL" })
			.inputValue();
		await page.getByRole("button", { name: "Dismiss secret" }).click();
		await expect(page.getByLabel("Secret value")).toHaveCount(0);

		// The identity provider's first provisioning cycle pushes a group.
		const pushed = await api.post(`${new URL(tenantUrl).pathname}/Groups`, {
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/scim+json",
			},
			data: {
				schemas: ["urn:ietf:params:scim:schemas:core:2.0:Group"],
				displayName: GROUP_NAME,
				externalId: `entra-${SUFFIX}`,
			},
		});
		expect(pushed.status(), await pushed.text()).toBe(201);

		await page.reload();
		await page
			.getByRole("button", { name: `Edit mapping for ${GROUP_NAME}` })
			.click();
		const mapping = page.getByRole("dialog", { name: `Map ${GROUP_NAME}` });
		await mapping.getByRole("combobox", { name: "Access only" }).click();
		await page.getByRole("option", { name: ROLE_NAME }).click();
		// Close the role list without dismissing the dialog.
		await mapping.getByRole("heading", { name: `Map ${GROUP_NAME}` }).click();
		await mapping.getByRole("button", { name: "Save Mapping" }).click();
		await expect(mapping).toHaveCount(0);

		const row = page.getByRole("row", { name: new RegExp(GROUP_NAME) });
		await expect(row.getByText("Mapped", { exact: true })).toBeVisible();
		await expect(row.getByText(ROLE_NAME)).toBeVisible();
	});
});
