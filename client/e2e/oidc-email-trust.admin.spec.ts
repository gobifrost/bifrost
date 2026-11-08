import { expect, test } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

test("administrator explicitly enables and persists OIDC email trust", async ({
	api,
	page,
}) => {
	const initial = await api.get("/api/settings/oauth");
	expect(initial.ok()).toBe(true);
	const settings =
		(await initial.json()) as components["schemas"]["OAuthConfigListResponse"];
	expect(
		settings.providers.find((p) => p.provider === "oidc")?.configured,
		"Refuse to overwrite an existing OIDC provider",
	).toBe(false);
	let owned = false;
	try {
		await page.goto("/settings/sso");
		const card = page
			.locator('[data-slot="card"]')
			.filter({ has: page.getByText("OIDC Provider", { exact: true }) });
		const trust = card.getByRole("switch", {
			name: "Trust Provider Email for Account Linking",
		});
		await expect(trust).not.toBeChecked();
		await card
			.getByLabel("Discovery URL", { exact: true })
			.fill("https://idp.example/.well-known/openid-configuration");
		await card
			.getByLabel("Client ID", { exact: true })
			.fill("oidc-trust-browser-test");
		await card
			.getByLabel("Client Secret", { exact: true })
			.fill("oidc-trust-browser-secret");
		await trust.check();
		const saved = page.waitForResponse(
			(r) =>
				r.url().endsWith("/api/settings/oauth/oidc") &&
				r.request().method() === "PUT",
		);
		await card.getByRole("button", { name: "Save Configuration" }).click();
		const response = await saved;
		expect(response.ok()).toBe(true);
		owned = true;
		expect((await response.json()).trust_email_for_linking).toBe(true);
		await page.reload();
		await card.getByRole("button", { name: "Edit" }).click();
		await expect(trust).toBeChecked();
		await expect(
			card.getByText(/prevents users from choosing another person/),
		).toBeVisible();
	} finally {
		if (owned) {
			const deleted = await api.delete("/api/settings/oauth/oidc");
			expect(deleted.ok()).toBe(true);
		}
	}
});
