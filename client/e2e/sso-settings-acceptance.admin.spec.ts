import { generateKeyPairSync } from "node:crypto";
import type { Locator, Page } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import { expect, test } from "./fixtures/api-fixture";

type OAuthProvider = "microsoft" | "google" | "oidc";

type OAuthLoginPreference = {
	auto_redirect_to_sso: boolean;
	default_sso_provider: OAuthProvider | null;
};

type OAuthProviderConfig = {
	provider: OAuthProvider;
	configured: boolean;
	client_id?: string | null;
	client_secret_set?: boolean;
	token_endpoint_auth_method?: string;
	private_key_set?: boolean;
	private_key_kid?: string | null;
	scopes?: string[];
};

type OAuthConfigList = {
	providers: OAuthProviderConfig[];
	login_preference: OAuthLoginPreference;
};

const UNIQUE = `sso-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
const GOOGLE_CLIENT_ID_INITIAL = `${UNIQUE}-initial.apps.googleusercontent.com`;
const GOOGLE_CLIENT_ID_EDITED = `${UNIQUE}-edited.apps.googleusercontent.com`;
const GOOGLE_CLIENT_SECRET = `secret-${UNIQUE}`;
const GOOGLE_KID = `kid-${UNIQUE}`;
const GOOGLE_SCOPES = ["openid", "email", "groups"];

function testPrivateKeyPem(): string {
	const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
	return privateKey.export({ type: "pkcs8", format: "pem" }).toString();
}

async function chooseClientAuth(page: Page, label: string): Promise<void> {
	await page.getByRole("option", { name: label }).click();
}

async function readOAuthSettings(api: AuthedApi): Promise<OAuthConfigList> {
	const response = await api.get("/api/settings/oauth");
	expect(response.ok(), `read OAuth settings: ${response.status()}`).toBe(
		true,
	);
	return (await response.json()) as OAuthConfigList;
}

function googleConfig(
	settings: OAuthConfigList,
): OAuthProviderConfig | undefined {
	return settings.providers.find(
		(provider) => provider.provider === "google",
	);
}

async function restoreLoginPreference(
	api: AuthedApi,
	preference: OAuthLoginPreference,
): Promise<void> {
	const response = await api.put("/api/settings/oauth/login-preference", {
		data: preference,
	});
	expect(
		response.ok(),
		`restore login preference: ${response.status()}`,
	).toBe(true);
}

async function deleteOwnedGoogleConfig(api: AuthedApi): Promise<void> {
	const response = await api.delete("/api/settings/oauth/google");
	expect(
		[200, 204, 404].includes(response.status()),
		`delete owned Google config: ${response.status()}`,
	).toBe(true);
}

function googleCard(page: Page): Locator {
	return page.locator('[data-slot="card"]').filter({
		has: page.getByText(
			"Allow users to sign in with their Google accounts.",
		),
	});
}

async function saveGoogleConfig(page: Page, card: Locator): Promise<void> {
	const responsePromise = page.waitForResponse(
		(response) =>
			response.url().includes("/api/settings/oauth/google") &&
			response.request().method() === "PUT",
	);
	await card.getByRole("button", { name: "Save Configuration" }).click();
	const response = await responsePromise;
	expect(response.ok(), `save Google config: ${response.status()}`).toBe(
		true,
	);
	await expect(card.getByText("Configured", { exact: true })).toBeVisible();
}

async function choosePreferredProvider(
	page: Page,
	providerName: string,
): Promise<void> {
	await page.locator("#preferred-sso-provider").click();
	await page.getByRole("option", { name: providerName }).click();
}

test.describe.serial("SSO settings acceptance", () => {
	let originalPreference: OAuthLoginPreference | null = null;
	let ownsGoogleConfig = false;

	test.afterAll(async ({ api }) => {
		if (originalPreference) {
			await restoreLoginPreference(api, originalPreference);
		}
		if (ownsGoogleConfig) {
			await deleteOwnedGoogleConfig(api);
		}
	});

	test("creates, edits, prefers, persists, and removes Google SSO through settings", async ({
		api,
		page,
	}) => {
		const initialSettings = await readOAuthSettings(api);
		originalPreference = initialSettings.login_preference;

		const initialGoogle = googleConfig(initialSettings);
		expect(
			initialGoogle?.configured ||
				Boolean(initialGoogle?.client_id) ||
				Boolean(initialGoogle?.client_secret_set),
			"Google SSO is already configured; refusing to overwrite unknown credentials",
		).toBe(false);

		await page.goto("/settings/sso");
		await expect(
			page.getByText("Preferred sign-in", { exact: true }),
		).toBeVisible();

		const card = googleCard(page);
		await expect(card.getByText("Google", { exact: true })).toBeVisible();
		await card.getByLabel("Client ID").fill(GOOGLE_CLIENT_ID_INITIAL);
		await card.getByLabel("Client Secret").fill(GOOGLE_CLIENT_SECRET);
		await saveGoogleConfig(page, card);
		ownsGoogleConfig = true;

		let persisted = googleConfig(await readOAuthSettings(api));
		expect(persisted?.configured).toBe(true);
		expect(persisted?.client_id).toBe(GOOGLE_CLIENT_ID_INITIAL);
		expect(persisted?.client_secret_set).toBe(true);

		await card.getByRole("button", { name: "Edit" }).click();
		await card.getByLabel("Client ID").fill(GOOGLE_CLIENT_ID_EDITED);
		await card.getByLabel("Client Secret").fill(GOOGLE_CLIENT_SECRET);
		await saveGoogleConfig(page, card);

		persisted = googleConfig(await readOAuthSettings(api));
		expect(persisted?.client_id).toBe(GOOGLE_CLIENT_ID_EDITED);

		// Switch to private_key_jwt, supply a write-only key and custom scopes.
		await card.getByRole("button", { name: "Edit" }).click();
		await card.getByLabel("Client authentication").click();
		await chooseClientAuth(page, "Private key JWT");
		await card.getByLabel("Private key (PEM)").fill(testPrivateKeyPem());
		await card.getByLabel("Key ID (kid)").fill(GOOGLE_KID);
		await card.getByLabel("Scopes").fill(GOOGLE_SCOPES.join(" "));
		await saveGoogleConfig(page, card);

		persisted = googleConfig(await readOAuthSettings(api));
		expect(persisted?.token_endpoint_auth_method).toBe("private_key_jwt");
		expect(persisted?.private_key_set).toBe(true);
		expect(persisted?.private_key_kid).toBe(GOOGLE_KID);
		expect(persisted?.scopes).toEqual(GOOGLE_SCOPES);

		// Revoke the stored key explicitly, back on a client-secret method.
		await card.getByRole("button", { name: "Edit" }).click();
		await card.getByLabel("Client authentication").click();
		await chooseClientAuth(page, "Client secret (POST body)");
		await card.getByLabel("Client Secret").fill(GOOGLE_CLIENT_SECRET);
		await card.getByLabel("Remove the stored private key").check();
		await saveGoogleConfig(page, card);

		persisted = googleConfig(await readOAuthSettings(api));
		expect(persisted?.token_endpoint_auth_method).toBe("client_secret_post");
		expect(persisted?.private_key_set).toBe(false);
		expect(persisted?.private_key_kid ?? null).toBeNull();

		await page.reload();
		const reloadedCard = googleCard(page);
		await expect(
			reloadedCard.getByText("Configured", { exact: true }),
		).toBeVisible();
		await expect(
			reloadedCard.getByText(GOOGLE_CLIENT_ID_EDITED, { exact: true }),
		).toBeVisible();

		const preferSso = page.getByLabel("Prefer SSO on login");
		if (await preferSso.isChecked()) {
			await preferSso.click();
		}
		await choosePreferredProvider(page, "Google");
		const preferenceResponsePromise = page.waitForResponse(
			(response) =>
				response
					.url()
					.includes("/api/settings/oauth/login-preference") &&
				response.request().method() === "PUT",
		);
		await page.getByRole("button", { name: "Save preference" }).click();
		const preferenceResponse = await preferenceResponsePromise;
		expect(
			preferenceResponse.ok(),
			`save login preference: ${preferenceResponse.status()}`,
		).toBe(true);

		let settings = await readOAuthSettings(api);
		expect(settings.login_preference).toMatchObject({
			auto_redirect_to_sso: false,
			default_sso_provider: "google",
			password_login_enabled: true,
			passkey_login_enabled: true,
		});

		await page.reload();
		await expect(page.locator("#preferred-sso-provider")).toContainText(
			"Google",
		);
		expect(await page.getByLabel("Prefer SSO on login").isChecked()).toBe(
			false,
		);

		const deleteResponsePromise = page.waitForResponse(
			(response) =>
				response.url().includes("/api/settings/oauth/google") &&
				response.request().method() === "DELETE",
		);
		await googleCard(page).getByRole("button", { name: "Remove" }).click();
		await page
			.getByRole("button", { name: "Remove Configuration" })
			.click();
		const deleteResponse = await deleteResponsePromise;
		expect(
			[200, 204, 404].includes(deleteResponse.status()),
			`remove Google config through UI: ${deleteResponse.status()}`,
		).toBe(true);
		ownsGoogleConfig = false;

		settings = await readOAuthSettings(api);
		persisted = googleConfig(settings);
		expect(persisted?.configured ?? false).toBe(false);
		expect(persisted?.client_id ?? null).toBeNull();
		expect(persisted?.client_secret_set ?? false).toBe(false);
	});
});
