/**
 * Person access page — admin happy path.
 *
 * A Platform Admin opens a customer user from the Users list. The access map
 * shows the organization their role is placed on; the admin moves that role to
 * "All customer organizations" with the placement preset, saves, and the map
 * gains that row. The Roles list then shows the role's holders and placement.
 */

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const ORG_NAME = `Contoso ${SUFFIX}`;
const ROLE_NAME = `Fabrikam Reader ${SUFFIX}`;
const USER_EMAIL = `user-access-${SUFFIX}@e2e.gobifrost.dev`;
const USER_NAME = `Access Person ${SUFFIX}`;

test.describe("Person access page", () => {
	let organizationId: string;
	let roleId: string;
	let userId: string;

	test.beforeAll(async ({ api }) => {
		const org = await api.post("/api/organizations", {
			data: {
				name: ORG_NAME,
				domain: `contoso-${SUFFIX}.gobifrost.dev`,
			},
		});
		expect(org.ok(), `create organization: ${await org.text()}`).toBe(true);
		organizationId = ((await org.json()) as { id: string }).id;

		const role = await api.post("/api/roles", {
			data: { name: ROLE_NAME, description: "e2e person access page" },
		});
		expect(role.ok(), `create role: ${await role.text()}`).toBe(true);
		roleId = ((await role.json()) as { id: string }).id;
		const permissions = await api.put(`/api/roles/${roleId}/permissions`, {
			data: { permissions: ["users.read"] },
		});
		expect(
			permissions.ok(),
			`set role permissions: ${await permissions.text()}`,
		).toBe(true);

		const user = await api.post("/api/users", {
			data: {
				email: USER_EMAIL,
				name: USER_NAME,
				organization_id: organizationId,
				is_superuser: false,
				invite: false,
			},
		});
		expect(user.ok(), `create user: ${await user.text()}`).toBe(true);
		userId = ((await user.json()) as { id: string }).id;

		const current = await api.get(`/api/users/${userId}/role-assignments`);
		expect(current.ok(), `read assignments: ${current.status()}`).toBe(
			true,
		);
		const baseRoleId = (
			(await current.json()) as { base_role: { id: string } }
		).base_role.id;
		const placed = await api.put(`/api/users/${userId}/role-assignments`, {
			data: {
				base_role_id: baseRoleId,
				additional: [
					{
						role_id: roleId,
						boundaries: [
							{
								kind: "organization",
								organization_id: organizationId,
							},
						],
					},
				],
			},
		});
		expect(placed.ok(), `place role: ${await placed.text()}`).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		const removals = [
			userId && `/api/users/${userId}`,
			roleId && `/api/roles/${roleId}`,
			organizationId && `/api/organizations/${organizationId}`,
		].filter((path): path is string => !!path);
		for (const path of removals) {
			const deleted = await api.delete(path);
			expect([200, 204, 404]).toContain(deleted.status());
		}
	});

	test("places a role on all customer organizations and sees it in the map and the Roles list", async ({
		page,
	}) => {
		await page.goto("/users");
		await page
			.getByPlaceholder("Search users by email or name...")
			.fill(USER_EMAIL);
		await page
			.getByRole("row")
			.filter({ hasText: USER_EMAIL })
			.getByText(USER_EMAIL, { exact: true })
			.click();
		await expect(page).toHaveURL(new RegExp(`/users/${userId}$`));
		await expect(
			page.getByRole("heading", { name: USER_NAME }),
		).toBeVisible();

		const map = page.getByRole("table", { name: "Access by place" });
		await expect(
			map.getByRole("rowheader", { name: new RegExp(ORG_NAME) }),
		).toBeVisible();
		await expect(
			map.getByRole("rowheader", { name: "All customer organizations" }),
		).toHaveCount(0);

		await page
			.getByRole("radiogroup", { name: `Placement for ${ROLE_NAME}` })
			.getByRole("radio", { name: "All customer organizations" })
			.click();
		await page.getByRole("button", { name: "Save roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();

		await expect(
			map.getByRole("rowheader", { name: "All customer organizations" }),
		).toBeVisible();

		await page.reload();
		await expect(
			page
				.getByRole("table", { name: "Access by place" })
				.getByRole("rowheader", { name: "All customer organizations" }),
		).toBeVisible();

		await page.goto("/roles");
		await page
			.getByPlaceholder(/search roles by name or description/i)
			.fill(ROLE_NAME);
		const row = page.getByRole("row", { name: new RegExp(ROLE_NAME) });
		const headers = await page.getByRole("columnheader").allTextContents();
		const holdersColumn = headers.findIndex(
			(header) => header.trim() === "Holders",
		);
		expect(holdersColumn).toBeGreaterThanOrEqual(0);
		await expect(row.getByRole("cell").nth(holdersColumn)).toHaveText("1");
		await expect(
			row
				.getByRole("list", { name: `Where ${ROLE_NAME} applies` })
				.getByText("All customer organizations"),
		).toBeVisible();
	});
});
