import { test, expect } from "./fixtures/api-fixture";
import { knownUserCredentials } from "./fixtures/human-auth";
import type { components } from "../src/lib/v1";

test("compact exclusion pickers and detail settings share the protected login policy", async ({
	page,
	api,
}, testInfo) => {
	const admin = knownUserCredentials("platform_admin");
	const person = knownUserCredentials("org1_user");
	const response = await api.get("/api/settings/oauth");
	expect(response.ok()).toBe(true);
	const before =
		(await response.json()) as components["schemas"]["OAuthConfigListResponse"];
	const ownsProvider = !before.providers.some(
		(provider) => provider.configured,
	);
	const orgResponse = await api.get(
		`/api/organizations/${person.organizationId}`,
	);
	expect(orgResponse.ok()).toBe(true);
	const org = await orgResponse.json();
	const capture = async (name: string) =>
		page.screenshot({
			path: testInfo.outputPath(`${name}.png`),
			fullPage: true,
			animations: "disabled",
		});
	try {
		if (ownsProvider)
			expect(
				(
					await api.put("/api/settings/oauth/google", {
						data: {
							client_id: "sso-exclusion-demo",
							client_secret: "synthetic-demo-secret",
						},
					})
				).ok(),
			).toBe(true);
		expect(
			(
				await api.put("/api/settings/oauth/login-preference", {
					data: {
						...before.login_preference,
						auto_redirect_to_sso: false,
						default_sso_provider: null,
						password_login_enabled: false,
						passkey_login_enabled: false,
						excluded_user_ids: [admin.userId],
						excluded_organization_ids: [],
					},
				})
			).ok(),
		).toBe(true);
		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.goto("/settings/sso");
		const users = page.getByRole("combobox", {
			name: "Excluded Users",
			exact: true,
		});
		const organizations = page.getByRole("combobox", {
			name: "Excluded Organizations",
			exact: true,
		});
		const save = page.getByRole("button", {
			name: "Save Exclusions",
			exact: true,
		});
		const saveSelection = async () => {
			const saved = page.waitForResponse(
				(response) =>
					response
						.url()
						.endsWith("/api/settings/oauth/login-preference") &&
					response.request().method() === "PUT",
			);
			await save.click();
			expect((await saved).ok()).toBe(true);
			await expect(save).toHaveText("Save Exclusions");
			await expect(save).toBeDisabled();
		};

		await expect(users).toBeEnabled();
		await expect(organizations).toBeEnabled();
		await users.scrollIntoViewIfNeeded();
		await capture("authentication-settings-desktop");
		await users.click();
		await page.getByPlaceholder("Search users…").fill(person.email);
		await capture("user-multiselect-desktop");
		await page
			.getByRole("option")
			.filter({ hasText: person.email })
			.click();
		await page.keyboard.press("Escape");
		await saveSelection();
		await page.goto(`/users/${person.userId}/profile`);
		const userToggle = page.getByRole("switch", {
			name: "Exclude From SSO Enforcement",
			exact: true,
		});
		await expect(userToggle).toBeChecked();
		await page
			.getByRole("region", { name: "Sign-In Settings", exact: true })
			.scrollIntoViewIfNeeded();
		await capture("user-sign-in-desktop");
		await page.setViewportSize({ width: 390, height: 844 });
		await userToggle.scrollIntoViewIfNeeded();
		await capture("user-sign-in-mobile");
		await userToggle.focus();
		await page.keyboard.press("Space");
		await expect(userToggle).not.toBeChecked();
		await expect(userToggle).toBeEnabled();

		await page.setViewportSize({ width: 1440, height: 1000 });
		await page.goto("/settings/sso");
		await organizations.click();
		await page.getByPlaceholder("Search organizations…").fill(org.name);
		await capture("organization-multiselect-desktop");
		await page.getByRole("option").filter({ hasText: org.name }).click();
		await page.keyboard.press("Escape");
		await saveSelection();

		await page.goto("/organizations");
		await page
			.getByRole("button", { name: `Edit ${org.name}`, exact: true })
			.click();
		const dialog = page.getByRole("dialog", {
			name: "Edit Organization",
			exact: true,
		});
		const orgToggle = dialog.getByRole("switch", {
			name: "Exclude From SSO Enforcement",
			exact: true,
		});
		await expect(orgToggle).toBeChecked();
		await orgToggle.scrollIntoViewIfNeeded();
		await capture("organization-sign-in-desktop");
		await page.setViewportSize({ width: 390, height: 844 });
		await orgToggle.scrollIntoViewIfNeeded();
		await capture("organization-sign-in-mobile");
		await orgToggle.click();
		await expect(orgToggle).not.toBeChecked();
		await expect(orgToggle).toBeEnabled();
		await dialog
			.getByRole("button", { name: "Cancel", exact: true })
			.click();

		await page.goto("/settings/sso");
		await organizations.scrollIntoViewIfNeeded();
		await capture("authentication-settings-mobile");
		await users.click();
		await page.getByPlaceholder("Search users…").fill(admin.email);
		await capture("user-multiselect-mobile");
		await page.getByRole("option").filter({ hasText: admin.email }).click();
		await page.keyboard.press("Escape");
		await save.click();
		await expect(
			page
				.getByRole("alert")
				.filter({ hasText: "active platform admin" }),
		).toBeVisible();
		await page
			.getByRole("alert")
			.filter({ hasText: "active platform admin" })
			.scrollIntoViewIfNeeded();
		await capture("recovery-admin-guard-mobile");
	} finally {
		expect(
			(
				await api.put("/api/settings/oauth/login-preference", {
					data: before.login_preference,
				})
			).ok(),
		).toBe(true);
		if (ownsProvider)
			expect((await api.delete("/api/settings/oauth/google")).ok()).toBe(
				true,
			);
	}
});
