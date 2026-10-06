/**
 * Role Assignments — admin happy path.
 *
 * A Platform Admin opens a person's page, assigns Platform Operator with the
 * "All Customer Organizations" placement preset, saves, and sees the
 * assignment listed; after a reload the assignment is still there. Platform
 * Admin is an additional role too: it is added with its one placement, Global
 * (no preset to choose), and removed again from the same screen. Secrets
 * Reader is added the same way, at places the server fixes.
 */

import { test, expect } from "./fixtures/api-fixture";

const SUFFIX = Math.random().toString(36).slice(2, 8);
const USER_EMAIL = `role-assign-${SUFFIX}@e2e.gobifrost.dev`;
const USER_NAME = `Role Assign ${SUFFIX}`;

test.describe("Role Assignments", () => {
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
		await expect(
			page.getByRole("heading", { name: USER_NAME }),
		).toBeVisible({ timeout: 10000 });
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Add Role" }).click();
		await page.getByRole("option", { name: /Platform Operator/ }).click();
		await page
			.getByRole("radiogroup", {
				name: "Placement for Platform Operator",
			})
			.getByRole("radio", { name: "All Customer Organizations" })
			.click();
		await expect(
			operatorPlaces().getByText("All Customer Organizations"),
		).toBeVisible();

		const save = page.getByRole("button", { name: "Save Roles" });
		await save.click();
		await expect(page.getByText("Roles saved")).toBeVisible();
		await expect(save).toBeDisabled();
		await expect(page.getByText(/^Protected: holds/)).toBeVisible();

		await page.reload();
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible({ timeout: 10000 });
		await expect(
			operatorPlaces().getByText("All Customer Organizations"),
		).toBeVisible();
	});

	test("adds and removes Platform Admin as an additional role", async ({
		page,
	}) => {
		const adminPlaces = () =>
			page.getByRole("list", { name: "Where Platform Admin applies" });

		await page.goto(`/users/${userId}`);
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible({ timeout: 10000 });

		await page.getByRole("button", { name: "Add Role" }).click();
		await page.getByRole("option", { name: /^Platform Admin/ }).click();
		await expect(adminPlaces().getByText("Global")).toBeVisible();
		await expect(
			page.getByRole("radiogroup", {
				name: "Placement for Platform Admin",
			}),
		).toHaveCount(0);
		await page.getByRole("button", { name: "Save Roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();

		await page.reload();
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible({ timeout: 10000 });
		await expect(adminPlaces().getByText("Global")).toBeVisible();

		await page
			.getByRole("button", { name: "Remove Platform Admin" })
			.click();
		await page.getByRole("button", { name: "Save Roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();
		await expect(adminPlaces()).toHaveCount(0);
	});

	test("adds Secrets Reader at its fixed places", async ({ page }) => {
		await page.goto(`/users/${userId}`);
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible({ timeout: 10000 });

		await page.getByRole("button", { name: "Add Role" }).click();
		await page.getByRole("option", { name: /Secrets Reader/ }).click();
		await expect(page.getByText("Applies everywhere.")).toBeVisible();
		await expect(
			page.getByRole("button", {
				name: "Add where Secrets Reader applies",
			}),
		).toHaveCount(0);
		await expect(
			page.getByRole("radiogroup", {
				name: "Placement for Secrets Reader",
			}),
		).toHaveCount(0);
		await page.getByRole("button", { name: "Save Roles" }).click();
		await expect(page.getByText("Roles saved")).toBeVisible();

		await page.reload();
		await expect(
			page.getByRole("heading", { name: "Additional Roles" }),
		).toBeVisible({ timeout: 10000 });
		await expect(page.getByText("Applies everywhere.")).toBeVisible();
	});
});
