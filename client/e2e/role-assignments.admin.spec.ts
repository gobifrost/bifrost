/**
 * Roles & access — admin happy path.
 *
 * A Platform Admin opens a user, assigns Platform Operator for all customer
 * organizations, saves, and sees the assignment listed; after a reload the
 * assignment is still there. Platform Admin is an additional role too: it is
 * added platform-wide and removed again from the same screen. Secrets Reader
 * is added the same way, at places the server fixes.
 */

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const USER_EMAIL = `role-assign-${SUFFIX}@e2e.gobifrost.dev`;
const USER_NAME = `Role Assign ${SUFFIX}`;

test.describe("Roles & access", () => {
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

	test("assigns Platform Operator for all customer organizations", async ({
		page,
	}) => {
		const operatorPlaces = () =>
			page.getByRole("list", { name: "Where Platform Operator applies" });

		await page.goto(`/users/${userId}`);
		const dialog = page.getByRole("dialog", { name: /edit user/i });
		await expect(dialog).toBeVisible({ timeout: 10000 });
		await dialog.getByRole("tab", { name: "Roles & access" }).click();
		await expect(
			dialog.getByRole("heading", { name: "Additional roles" }),
		).toBeVisible();

		await dialog.getByRole("button", { name: "Add role" }).click();
		await page.getByRole("option", { name: /Platform Operator/ }).click();
		await expect(
			operatorPlaces().getByText("In all customer organizations"),
		).toBeVisible();

		const save = dialog.getByRole("button", { name: "Save roles" });
		await save.click();
		await expect(page.getByText("Roles saved")).toBeVisible();
		await expect(save).toBeDisabled();
		await expect(
			dialog.getByText("Protected", { exact: true }),
		).toBeVisible();

		await page.reload();
		const reopened = page.getByRole("dialog", { name: /edit user/i });
		await expect(reopened).toBeVisible({ timeout: 10000 });
		await reopened.getByRole("tab", { name: "Roles & access" }).click();
		await expect(
			operatorPlaces().getByText("In all customer organizations"),
		).toBeVisible();
	});

	test("adds and removes Platform Admin as an additional role", async ({
		page,
	}) => {
		const adminPlaces = () =>
			page.getByRole("list", { name: "Where Platform Admin applies" });

		await page.goto(`/users/${userId}`);
		const dialog = page.getByRole("dialog", { name: /edit user/i });
		await expect(dialog).toBeVisible({ timeout: 10000 });
		await dialog.getByRole("tab", { name: "Roles & access" }).click();

		await dialog.getByRole("button", { name: "Add role" }).click();
		await page.getByRole("option", { name: /^Platform Admin/ }).click();
		await expect(adminPlaces().getByText("Platform-wide")).toBeVisible();
		await dialog.getByRole("button", { name: "Save roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();

		await page.reload();
		const reopened = page.getByRole("dialog", { name: /edit user/i });
		await expect(reopened).toBeVisible({ timeout: 10000 });
		await reopened.getByRole("tab", { name: "Roles & access" }).click();
		await expect(adminPlaces().getByText("Platform-wide")).toBeVisible();

		await reopened
			.getByRole("button", { name: "Remove Platform Admin" })
			.click();
		await reopened.getByRole("button", { name: "Save roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();
		await expect(adminPlaces()).toHaveCount(0);
	});

	test("adds Secrets Reader at its fixed places", async ({ page }) => {
		await page.goto(`/users/${userId}`);
		const dialog = page.getByRole("dialog", { name: /edit user/i });
		await expect(dialog).toBeVisible({ timeout: 10000 });
		await dialog.getByRole("tab", { name: "Roles & access" }).click();

		await dialog.getByRole("button", { name: "Add role" }).click();
		await page.getByRole("option", { name: /Secrets Reader/ }).click();
		await expect(dialog.getByText("Applies everywhere.")).toBeVisible();
		await expect(
			dialog.getByRole("button", {
				name: "Add where Secrets Reader applies",
			}),
		).toHaveCount(0);
		await dialog.getByRole("button", { name: "Save roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();

		await page.reload();
		const reopened = page.getByRole("dialog", { name: /edit user/i });
		await expect(reopened).toBeVisible({ timeout: 10000 });
		await reopened.getByRole("tab", { name: "Roles & access" }).click();
		await expect(reopened.getByText("Applies everywhere.")).toBeVisible();
	});
});
