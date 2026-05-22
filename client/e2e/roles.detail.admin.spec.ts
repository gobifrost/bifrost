/**
 * Roles detail + consumer tabs — admin (Block 4/5).
 *
 * Drives the new card-grid + RoleDetail page + AssignDrawer flow:
 *  - Roles list shows cards with chips
 *  - Click a chip → land on the right tab
 *  - Open Assign drawer for users → tick a user → submit
 *  - Verify the user appears in the assigned list
 *  - Multi-select + bulk unassign drops them back to empty
 */

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const ROLE_NAME = `RoleDetail ${SUFFIX}`;
const USER_EMAIL = `roledetail-${SUFFIX}@e2e.gobifrost.dev`;

test.describe("Roles detail", () => {
	let roleId: string;
	let userId: string;

	test.beforeAll(async ({ api }) => {
		// Create a fresh role.
		const r = await api.post("/api/roles", {
			data: { name: ROLE_NAME, description: "e2e detail page" },
		});
		expect(r.ok(), await r.text()).toBe(true);
		roleId = (await r.json()).id;

		// Find / create an org we can put the user in.
		const orgsResp = await api.get("/api/organizations");
		const orgs = (await orgsResp.json()) as { id: string }[];
		const orgId = orgs[0]?.id;
		expect(orgId, "need at least one org").toBeTruthy();

		const u = await api.post("/api/users", {
			data: {
				email: USER_EMAIL,
				name: `RoleDetail User ${SUFFIX}`,
				organization_id: orgId,
				is_superuser: false,
				invite: false,
			},
		});
		expect(u.ok(), await u.text()).toBe(true);
		userId = (await u.json()).id;
	});

	test.afterAll(async ({ api }) => {
		if (userId) await api.delete(`/api/users/${userId}`);
		if (roleId) await api.delete(`/api/roles/${roleId}`);
	});

	test("card → users chip → drawer → assigned → unassign", async ({ page }) => {
		await page.goto("/roles");

		// The card for our role is rendered with name visible.
		await expect(page.getByText(ROLE_NAME).first()).toBeVisible({ timeout: 10000 });

		// Navigate directly via the URL — the click-the-chip path is exercised
		// in the vitest. This spec focuses on the assignment lifecycle, not on
		// re-validating the chip nav.
		await page.goto(`/roles/${roleId}/users`);

		// We're on the detail page.
		await expect(
			page.getByRole("heading", { name: ROLE_NAME }),
		).toBeVisible();

		// No users assigned yet.
		await expect(
			page.getByText(/no users assigned to this role yet/i),
		).toBeVisible();

		// Open the drawer.
		await page.getByRole("button", { name: /assign users/i }).click();
		await expect(
			page.getByText(/pick the users you want to add/i),
		).toBeVisible();

		// Tick our seeded user (drawer renders candidates).
		const pickRow = page.getByLabel(new RegExp(`Pick .*${SUFFIX}`));
		await pickRow.first().click();

		await page.getByRole("button", { name: /assign 1/i }).click();

		// Toast confirms.
		await expect(page.getByText(/assigned 1 users/i)).toBeVisible({
			timeout: 10000,
		});

		// Close the drawer (it stays open after submit per design) so the
		// underlying tab's checkbox isn't ambiguous with the drawer's.
		// Use the SheetClose icon-button (the dialog's primary close), not our
		// outline footer Close button — there are two "Close" buttons in the
		// drawer DOM.
		await page.getByRole("dialog").getByRole("button", { name: /^close$/i }).last().click();

		// User is now in the assigned list.
		await expect(
			page.getByText(new RegExp(`RoleDetail User ${SUFFIX}`)),
		).toBeVisible();

		// Tick + unassign.
		await page.getByLabel(new RegExp(`Select RoleDetail User ${SUFFIX}`)).click();
		await page.getByRole("button", { name: /unassign from role/i }).click();

		await expect(page.getByText(/removed 1 users/i)).toBeVisible({
			timeout: 10000,
		});
		await expect(
			page.getByText(/no users assigned to this role yet/i),
		).toBeVisible();
	});
});
