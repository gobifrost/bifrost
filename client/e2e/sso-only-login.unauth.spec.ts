import { test, expect } from "./fixtures/api-fixture";

test.describe("SSO-only login page", () => {
	test("hides password and passkey behind the platform admin expander", async ({
		page,
	}) => {
		await page.route("**/auth/status", async (route) => {
			const response = await route.fetch();
			const status = await response.json();
			await route.fulfill({
				response,
				json: {
					...status,
					password_login_enabled: false,
					passkey_login_enabled: false,
					auto_redirect_to_sso: false,
					default_sso_provider: null,
					oauth_providers: [
						{
							name: "microsoft",
							display_name: "Microsoft",
							icon: "microsoft",
						},
					],
				},
			});
		});

		await page.goto("/login");
		await expect(
			page.getByRole("button", { name: "Microsoft" }),
		).toBeVisible();
		await expect(page.getByLabel("Email")).not.toBeVisible();
		await expect(
			page.getByRole("button", { name: /sign in with passkey/i }),
		).not.toBeVisible();
		await expect(page.getByText("Excluded Account Sign-In")).toBeVisible();

		await page.getByText("Excluded Account Sign-In").click();
		await expect(page.getByLabel("Email")).toBeVisible();
		await expect(page.getByLabel("Password")).toBeVisible();
	});
});
