/**
 * One end-to-end provisioning journey: an organization override supplies a
 * custom no-chat base role, and the new account is denied Chat before the
 * browser can spend tokens or create a conversation.
 */
import { test, expect } from "./fixtures/api-fixture";
import {
	authenticateInBrowser,
	registerAndSetupMFA,
} from "./setup/auth-helpers";
import type { TestUser } from "./fixtures/users";

const suffix = Math.random().toString(36).slice(2, 8);
const organizationName = `Base Role Defaults ${suffix}`;
const roleName = `No Chat Base Role ${suffix}`;
const domain = `base-role-${suffix}.example.com`;
const email = `no-chat-${suffix}@${domain}`;
const person: TestUser = {
	key: `no-chat-${suffix}`,
	email,
	name: "No Chat User",
	password: `NoChat-${suffix}!Aa1`,
	isSuperuser: false,
};

let organizationId = "";
let roleId = "";
let userId = "";

test.describe("Base role defaults deny Chat without a token-bearing request", () => {
	test.beforeAll(async ({ api }) => {
		const organization = await api.post("/api/organizations", {
			data: {
				name: organizationName,
				domain,
			},
		});
		expect(organization.ok(), await organization.text()).toBe(true);
		organizationId = ((await organization.json()) as { id: string }).id;

		const role = await api.post("/api/roles", {
			data: {
				name: roleName,
				description: "A base role intentionally without chat.",
			},
		});
		expect(role.ok(), await role.text()).toBe(true);
		roleId = ((await role.json()) as { id: string }).id;

		const rolePermissions = await api.put(
			`/api/roles/${roleId}/permissions`,
			{
				data: { permissions: [], runtime_permissions: [] },
			},
		);
		expect(rolePermissions.ok(), await rolePermissions.text()).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		if (userId) await api.delete(`/api/users/${userId}`);
		if (organizationId) {
			await api.put("/api/base-role-defaults", {
				params: { organization_id: organizationId },
				data: { default_base_role_id: null },
			});
			await api.delete(`/api/organizations/${organizationId}`);
		}
		if (roleId) await api.delete(`/api/roles/${roleId}`);
	});

	test("sets an organization default and keeps its provisioned user out of Chat", async ({
		api,
		page,
		browser,
	}, testInfo) => {
		await page.goto("/settings/base-role-defaults");
		await expect(
			page.getByRole("heading", { name: "Base Role Defaults" }),
		).toBeVisible();

		await page
			.getByRole("combobox", { name: "Organization scope" })
			.click();
		await page.getByRole("option", { name: organizationName }).click();
		await page.getByRole("combobox", { name: "Base role" }).click();
		await page.getByRole("option", { name: roleName }).click();
		await expect(page.getByRole("option", { name: roleName })).toBeHidden();
		await page.getByRole("button", { name: "Save Default" }).click();
		await expect(page.getByText("Base role default saved")).toBeVisible();
		await expect(
			page.getByText(/New accounts currently receive/),
		).toContainText(roleName);
		await page.screenshot({
			path: testInfo.outputPath("defaults-desktop.png"),
			fullPage: true,
		});
		await page.emulateMedia({ reducedMotion: "reduce" });
		await page.getByRole("button", { name: "Toggle theme" }).click();
		await expect(page.locator("html")).not.toHaveClass(/dark/);
		await page.screenshot({
			path: testInfo.outputPath("defaults-desktop-light.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(
			page.getByText(/New accounts currently receive/),
		).toContainText(roleName);
		expect(
			await page.evaluate(
				() => document.documentElement.scrollWidth <= window.innerWidth,
			),
		).toBe(true);
		await page.screenshot({
			path: testInfo.outputPath("defaults-mobile.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: "Toggle theme" }).click();
		await expect(page.locator("html")).toHaveClass(/dark/);
		await page.screenshot({
			path: testInfo.outputPath("defaults-mobile-dark.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 1280, height: 720 });

		const defaultResponse = await api.get("/api/base-role-defaults", {
			params: { organization_id: organizationId },
		});
		expect(defaultResponse.ok(), await defaultResponse.text()).toBe(true);
		expect(
			((await defaultResponse.json()) as { default_base_role_id: string })
				.default_base_role_id,
		).toBe(roleId);

		const userResponse = await api.post("/api/users", {
			data: {
				email: person.email,
				name: person.name,
				organization_id: organizationId,
				is_active: true,
				is_superuser: false,
				invite: false,
			},
		});
		expect(userResponse.ok(), await userResponse.text()).toBe(true);
		userId = ((await userResponse.json()) as { id: string }).id;

		const assignments = await api.get(
			`/api/users/${userId}/role-assignments`,
		);
		expect(assignments.ok(), await assignments.text()).toBe(true);
		expect(
			((await assignments.json()) as { base_role: { id: string } })
				.base_role.id,
		).toBe(roleId);

		const credentials = await registerAndSetupMFA(person);
		const context = await browser.newContext({
			baseURL: process.env.TEST_BASE_URL || "http://localhost:3000",
			storageState: { cookies: [], origins: [] },
		});
		try {
			const noChatPage = await context.newPage();
			const chatRequests: string[] = [];
			noChatPage.on("request", (request) => {
				if (new URL(request.url()).pathname.startsWith("/api/chat"))
					chatRequests.push(request.url());
			});
			await authenticateInBrowser(noChatPage, credentials);
			await noChatPage.goto("/chat");
			await expect(
				noChatPage.getByRole("heading", {
					name: "You don’t have access",
				}),
			).toBeVisible();
			expect(chatRequests).toEqual([]);
		} finally {
			await context.close();
		}
	});
});
