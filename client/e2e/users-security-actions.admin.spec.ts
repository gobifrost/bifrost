/**
 * Users — security actions, admin happy path.
 *
 * A Platform Admin resets a user's MFA from the row menu: the confirm dialog
 * says what is removed, and the success toast reports it.
 */

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const USER_EMAIL = `mfa-reset-${SUFFIX}@e2e.gobifrost.dev`;
const USER_NAME = `Mfa Reset ${SUFFIX}`;

test.describe("Users security actions", () => {
	let userId: string;

	test.beforeAll(async ({ api }) => {
		const orgs = await api.get("/api/organizations");
		expect(orgs.ok(), `list organizations: ${orgs.status()}`).toBe(true);
		const provider = (
			(await orgs.json()) as { id: string; is_provider: boolean }[]
		).find((org) => org.is_provider);
		expect(provider, "provider organization").toBeTruthy();

		const created = await api.post("/api/users", {
			data: {
				email: USER_EMAIL,
				name: USER_NAME,
				organization_id: provider!.id,
				is_superuser: false,
				invite: false,
			},
		});
		expect(created.ok(), `create user: ${created.status()}`).toBe(true);
		userId = ((await created.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (!userId) return;
		const deleted = await api.delete(`/api/users/${userId}`);
		expect([200, 204, 404]).toContain(deleted.status());
	});

	test("resets a user's MFA from the row menu", async ({ page }) => {
		await page.goto("/users");
		await page
			.getByPlaceholder("Search users by email or name...")
			.fill(USER_EMAIL);
		await page
			.getByRole("button", { name: `${USER_NAME} actions` })
			.click();
		await page.getByRole("menuitem", { name: "Reset MFA" }).click();

		const dialog = page.getByRole("alertdialog");
		await expect(dialog).toContainText(
			"authenticator app, recovery codes, passkeys and remembered devices",
		);
		await dialog.getByRole("button", { name: "Reset MFA" }).click();

		await expect(page.getByText(`MFA reset for ${USER_NAME}`)).toBeVisible();
		await expect(dialog).toBeHidden();
	});
});
