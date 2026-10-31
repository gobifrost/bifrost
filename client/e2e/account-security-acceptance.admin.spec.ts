/**
 * Account Security Acceptance (Admin)
 *
 * Verifies the real browser/WebAuthn passkey management path on an isolated
 * test-owned user. The shared admin session is only used for final user cleanup;
 * passkey registration, list persistence, and deletion run in the user's own
 * browser context with Chromium's virtual authenticator.
 */

import { randomUUID } from "node:crypto";
import type {
	APIRequestContext,
	APIResponse,
	BrowserContext,
	Page,
} from "./fixtures/api-fixture";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";
import { generateTOTP } from "./setup/totp";

const UNIQUE = `${Date.now()}-${process.pid}-${randomUUID().slice(0, 8)}`;
const USER_EMAIL = `acct-security-${UNIQUE}@gobifrost.dev`;
const USER_NAME = `Account Security ${UNIQUE}`;
const USER_PASSWORD = `AcctSecurity${UNIQUE}!Aa1`;
const PASSKEY_NAME = `E2E Passkey ${UNIQUE}`;
const BASE_URL = process.env.TEST_BASE_URL || "http://localhost:3000";

type RegisteredUser = {
	id: string;
	totpSecret: string;
};

type PasskeyList = {
	passkeys: Array<{ id: string; name: string }>;
	count: number;
};

async function expectOk(response: APIResponse, method: string, path: string) {
	expect(
		response.ok(),
		`${method.toUpperCase()} ${path} failed with ${response.status()}`,
	).toBe(true);
}

async function registerIsolatedUser(
	request: APIRequestContext,
	onRegistered: (userId: string) => void,
): Promise<RegisteredUser> {
	const registerResp = await request.post("/auth/register", {
		data: {
			email: USER_EMAIL,
			password: USER_PASSWORD,
			name: USER_NAME,
		},
	});
	await expectOk(registerResp, "POST", "/auth/register");
	const registered = (await registerResp.json()) as { id: string };
	expect(registered.id, "registered user response includes id").toBeTruthy();
	onRegistered(registered.id);

	const loginResp = await request.post("/auth/login", {
		form: {
			username: USER_EMAIL,
			password: USER_PASSWORD,
		},
	});
	await expectOk(loginResp, "POST", "/auth/login");
	const login = (await loginResp.json()) as { mfa_token?: string };
	expect(
		login.mfa_token,
		"password login returns MFA setup token",
	).toBeTruthy();

	const setupResp = await request.post("/auth/mfa/setup", {
		headers: { Authorization: `Bearer ${login.mfa_token}` },
	});
	await expectOk(setupResp, "POST", "/auth/mfa/setup");
	const setup = (await setupResp.json()) as { secret: string };
	expect(setup.secret, "MFA setup returns TOTP secret").toBeTruthy();

	const verifyResp = await request.post("/auth/mfa/verify", {
		headers: { Authorization: `Bearer ${login.mfa_token}` },
		data: { code: generateTOTP(setup.secret) },
	});
	await expectOk(verifyResp, "POST", "/auth/mfa/verify");

	return { id: registered.id, totpSecret: setup.secret };
}

async function loginIsolatedUser(page: Page, user: RegisteredUser) {
	await page.goto(new URL("/login", BASE_URL).toString());
	await expect(page.getByLabel("Email")).toBeVisible();
	await page.getByLabel("Email").fill(USER_EMAIL);
	await page.getByLabel("Password").fill(USER_PASSWORD);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();

	const mfaInput = page.getByLabel(/code|totp|verification/i);
	await expect(mfaInput).toBeVisible();
	await mfaInput.fill(generateTOTP(user.totpSecret));
	await page.getByRole("button", { name: /verify|submit|continue/i }).click();
	await page.waitForURL((url) => !url.pathname.startsWith("/login"));
}

async function installVirtualAuthenticator(
	context: BrowserContext,
	page: Page,
) {
	const cdp = await context.newCDPSession(page);
	await cdp.send("WebAuthn.enable");
	const { authenticatorId } = (await cdp.send(
		"WebAuthn.addVirtualAuthenticator",
		{
			options: {
				protocol: "ctap2",
				transport: "internal",
				hasResidentKey: true,
				hasUserVerification: true,
				isUserVerified: true,
				automaticPresenceSimulation: true,
			},
		},
	)) as { authenticatorId: string };
	return async () => {
		await cdp.send("WebAuthn.removeVirtualAuthenticator", {
			authenticatorId,
		});
		await cdp.send("WebAuthn.disable");
	};
}

async function listUserPasskeys(context: BrowserContext): Promise<PasskeyList> {
	const response = await context.request.get(
		new URL("/auth/passkeys", BASE_URL).toString(),
	);
	await expectOk(response, "GET", "/auth/passkeys");
	return (await response.json()) as PasskeyList;
}

async function cleanupUser(api: AuthedApi, userId: string) {
	const response = await api.delete(`/api/users/${userId}`);
	expect(
		[204, 404],
		`DELETE /api/users/{user_id} failed with ${response.status()}`,
	).toContain(response.status());
}

test.describe("Account security", () => {
	let userId = "";

	test.afterEach(async ({ api }) => {
		if (userId) await cleanupUser(api, userId);
		userId = "";
	});

	test("SECURITY-01 user registers, reloads, and removes a persisted passkey", async ({
		browser,
		request,
		api,
	}) => {
		const user = await registerIsolatedUser(request, (createdUserId) => {
			userId = createdUserId;
		});

		const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
		const page = await context.newPage();
		const removeAuthenticator = await installVirtualAuthenticator(
			context,
			page,
		);

		try {
			await loginIsolatedUser(page, user);
			await page.goto(
				new URL("/user-settings/security", BASE_URL).toString(),
			);
			await expect(
				page.getByRole("heading", { name: "User Settings" }),
			).toBeVisible();
			await expect(
				page.getByRole("heading", { name: "Passkeys" }),
			).toBeVisible();
			await expect(
				page.evaluate(() => ({
					secure: window.isSecureContext,
					publicKeyCredential: typeof window.PublicKeyCredential,
				})),
			).resolves.toEqual({
				secure: true,
				publicKeyCredential: "function",
			});

			await expect(page.getByText("No passkeys yet")).toBeVisible();
			await page
				.getByRole("button", { name: "Add Passkey", exact: true })
				.click();
			const addDialog = page.getByRole("dialog", { name: "Add Passkey" });
			await expect(addDialog).toBeVisible();
			await addDialog
				.getByLabel("Device Name (optional)")
				.fill(PASSKEY_NAME);
			await addDialog
				.getByRole("button", { name: "Register Passkey", exact: true })
				.click();
			await expect(addDialog).toBeHidden();
			await expect(
				page.getByText(PASSKEY_NAME, { exact: true }),
			).toBeVisible();
			await expect
				.poll(async () => {
					const passkeys = await listUserPasskeys(context);
					return passkeys.passkeys.some(
						(passkey) => passkey.name === PASSKEY_NAME,
					);
				})
				.toBe(true);

			await page.reload();
			await expect(
				page.getByRole("heading", { name: "Passkeys" }),
			).toBeVisible();
			await expect(
				page.getByText(PASSKEY_NAME, { exact: true }),
			).toBeVisible();

			await page
				.getByRole("button", { name: `Remove passkey ${PASSKEY_NAME}` })
				.click();
			const removeDialog = page.getByRole("alertdialog", {
				name: "Remove Passkey?",
			});
			await expect(removeDialog).toBeVisible();
			await expect(removeDialog.getByText(PASSKEY_NAME)).toBeVisible();
			await removeDialog
				.getByRole("button", { name: "Remove Passkey", exact: true })
				.click();
			await expect(removeDialog).toBeHidden();
			await expect(
				page.getByText(PASSKEY_NAME, { exact: true }),
			).toBeHidden();
			await expect
				.poll(async () => {
					const passkeys = await listUserPasskeys(context);
					return passkeys.passkeys.some(
						(passkey) => passkey.name === PASSKEY_NAME,
					);
				})
				.toBe(false);
		} finally {
			await removeAuthenticator();
			await context.close();
		}

		await cleanupUser(api, userId);
		userId = "";
	});
});
