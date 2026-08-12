import { expect, request as playwrightRequest, test } from "@playwright/test";
import { readFileSync } from "fs";
import { resolve } from "path";

const API_URL = process.env.TEST_API_URL || "http://api:8000";

test.describe.serial("Preferred SSO redirect", () => {
	let api: Awaited<ReturnType<typeof playwrightRequest.newContext>>;

	test.beforeAll(async () => {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as { platform_admin: { accessToken: string } };
		api = await playwrightRequest.newContext({
			baseURL: API_URL,
			extraHTTPHeaders: {
				Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
			},
		});

		const response = await api.put("/api/settings/oauth/microsoft", {
			data: {
				client_id: "playwright-preferred-login",
				client_secret: "playwright-secret",
				tenant_id: "organizations",
			},
		});
		expect(response.ok(), await response.text()).toBe(true);
	});

	test.afterAll(async () => {
		if (!api) return;
		await api.put("/api/settings/oauth/login-preference", {
			data: {
				auto_redirect_to_sso: false,
				default_sso_provider: null,
			},
		});
		await api.delete("/api/settings/oauth/microsoft");
		await api.dispose();
	});

	test("tries Microsoft once, then Back shows every login option", async ({
		browser,
	}) => {
		const context = await browser.newContext({
			storageState: { cookies: [], origins: [] },
		});
		// OAuth runs in HTTPS/localhost secure contexts in production. The
		// container-only http://client origin needs a minimal Web Crypto boundary.
		await context.addInitScript(() => {
			Object.defineProperty(window.crypto, "subtle", {
				value: {
					digest: async () => new Uint8Array(32).buffer,
				},
			});
		});
		const page = await context.newPage();
		let oauthInitRequests = 0;

		page.on("request", (request) => {
			if (request.url().includes("/auth/oauth/init/microsoft")) {
				oauthInitRequests += 1;
			}
		});

		await page.route("**/auth/status", async (route) => {
			const response = await route.fetch();
			const status = await response.json();
			await route.fulfill({
				response,
				json: {
					...status,
					auto_redirect_to_sso: true,
					default_sso_provider: "microsoft",
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

		await page.route("https://login.microsoftonline.com/**", async (route) => {
			await route.fulfill({
				contentType: "text/html",
				body: "<main><h1>Microsoft sign-in test boundary</h1></main>",
			});
		});

		await page.goto("/login?returnTo=/workflows");
		await expect(
			page.getByRole("heading", {
				name: "Microsoft sign-in test boundary",
			}),
		).toBeVisible();
		expect(oauthInitRequests).toBe(1);

		const providerUrl = new URL(page.url());
		expect(providerUrl.hostname).toBe("login.microsoftonline.com");
		expect(providerUrl.searchParams.get("redirect_uri")).toMatch(
			/\/auth\/callback\/microsoft$/,
		);

		await page.goBack();
		await expect(page.getByLabel("Email")).toBeVisible();
		await expect(page.getByLabel("Password")).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Microsoft" }),
		).toBeVisible();
		expect(oauthInitRequests).toBe(1);
		expect(
			await page.evaluate(() =>
				sessionStorage.getItem("oauth_redirect_from"),
			),
		).toBe("/workflows");

		await context.close();
	});
});
