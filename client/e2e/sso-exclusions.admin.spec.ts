import { test, expect } from "./fixtures/api-fixture";
import { knownUserCredentials } from "./fixtures/human-auth";
import type { components } from "../src/lib/v1";

test("user and organization exclusions share policy and preserve admin recovery", async ({
	page,
	api,
	browser,
}, testInfo) => {
	const admin = knownUserCredentials("platform_admin");
	const person = knownUserCredentials("org1_user");
	const beforeResponse = await api.get("/api/settings/oauth");
	expect(beforeResponse.ok()).toBe(true);
	const before =
		(await beforeResponse.json()) as components["schemas"]["OAuthConfigListResponse"];
	const ownsProvider = !before.providers.some(
		(provider) => provider.configured,
	);
	const context = await browser.newContext();
	try {
		if (ownsProvider) {
			const provider = await api.put("/api/settings/oauth/google", {
				data: {
					client_id: "sso-exclusion-demo",
					client_secret: "synthetic-demo-secret",
				},
			});
			expect(provider.ok()).toBe(true);
		}
		const set = await api.put("/api/settings/oauth/login-preference", {
			data: {
				...before.login_preference,
				auto_redirect_to_sso: false,
				default_sso_provider: null,
				password_login_enabled: false,
				passkey_login_enabled: false,
				excluded_user_ids: [admin.userId],
				excluded_organization_ids: [],
			},
		});
		expect(set.ok()).toBe(true);
		await page.goto("/settings/sso");
		await expect(
			page.getByRole("heading", { name: "SSO Exclusions", exact: true }),
		).toBeVisible();
		await page
			.getByText("Login Policy", { exact: true })
			.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("login-policy-settings.png"),
			fullPage: true,
		});
		await page.getByLabel("Find a User").fill(person.email);
		const exclusion = page.getByRole("switch", {
			name: "Exclude From SSO Enforcement",
			exact: true,
		});
		await expect(exclusion).toHaveCount(1);
		await exclusion.click();
		await expect(exclusion).toBeChecked();
		await page.screenshot({
			path: testInfo.outputPath("authentication-settings.png"),
			fullPage: true,
		});

		await page.goto(`/users/${person.userId}/profile`);
		const userExclusion = page.getByRole("switch", {
			name: "Exclude From SSO Enforcement",
			exact: true,
		});
		await expect(userExclusion).toBeChecked();
		await page.screenshot({
			path: testInfo.outputPath("user-exclusion.png"),
			fullPage: true,
		});
		const viewport = page.viewportSize();
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("user-exclusion-mobile.png"),
			fullPage: true,
		});
		if (viewport) await page.setViewportSize(viewport);
		await userExclusion.focus();
		await page.keyboard.press("Space");
		await expect(userExclusion).not.toBeChecked();

		await page.goto("/settings/sso");
		await page.getByLabel("Find a User").fill(admin.email);
		await expect(
			page.getByRole("switch", {
				name: "Exclude From SSO Enforcement",
				exact: true,
			}),
		).toHaveCount(1);
		await page
			.getByRole("switch", {
				name: "Exclude From SSO Enforcement",
				exact: true,
			})
			.click();
		await expect(
			page
				.getByRole("alert")
				.filter({ hasText: "active platform admin" }),
		).toBeVisible();
		await page.screenshot({
			path: testInfo.outputPath("recovery-admin-guard.png"),
			fullPage: true,
		});

		const orgResponse = await api.get(
			`/api/organizations/${person.organizationId}`,
		);
		expect(orgResponse.ok()).toBe(true);
		const org = await orgResponse.json();
		const orgSwitch = page.getByRole("switch", {
			name: org.name,
			exact: true,
		});
		await orgSwitch.click();
		await expect(orgSwitch).not.toBeChecked();
		await page.screenshot({
			path: testInfo.outputPath("organization-control.png"),
			fullPage: true,
		});
		await orgSwitch.click();
		await expect(orgSwitch).toBeChecked();

		const login = await context.newPage();
		await login.goto(new URL("/login", page.url()).toString());
		await login
			.getByText("Excluded Account Sign-In", { exact: true })
			.click();
		await expect(login.getByLabel("Email", { exact: true })).toBeVisible();
		await login.screenshot({
			path: testInfo.outputPath("login-policy.png"),
			fullPage: true,
		});
		await login.getByLabel("Email", { exact: true }).fill(person.email);
		await login
			.getByLabel("Password", { exact: true })
			.fill(person.password);
		await login
			.getByRole("button", { name: "Sign In", exact: true })
			.click();
		await expect(
			login.getByText(
				"Password login is disabled (SSO-only). Sign in with SSO.",
				{ exact: true },
			),
		).toBeVisible();
		await login.getByLabel("Password", { exact: true }).clear();
		await login.screenshot({
			path: testInfo.outputPath("login-policy-error.png"),
			fullPage: true,
		});
	} finally {
		await context.close();
		const restore = await api.put("/api/settings/oauth/login-preference", {
			data: before.login_preference,
		});
		expect(restore.ok()).toBe(true);
		if (ownsProvider)
			expect((await api.delete("/api/settings/oauth/google")).ok()).toBe(
				true,
			);
	}
});
