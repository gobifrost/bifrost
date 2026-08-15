/* eslint-disable no-console */
/**
 * Self-Healing Auth Fixture
 *
 * Provides authentication that works regardless of database state.
 * Tests can run independently without relying on a setup project.
 *
 * Strategy:
 * 1. Check if we have valid stored auth state
 * 2. If valid, use it
 * 3. If not, check /auth/status to see if system is configured
 * 4. If not configured, run full user setup
 * 5. If configured, login with known test credentials
 * 6. Save auth state for reuse
 */

import { Page, Browser, BrowserContext, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";
import { generateTOTP } from "../setup/totp";
import {
	USERS,
	ORGANIZATIONS,
	AUTH_STATE_DIR,
	getAuthStatePath,
	getCredentialsPath,
	type UserCredentials,
} from "./users";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// API URL - use environment variable or default
const API_URL = process.env.TEST_API_URL || "http://api:8000";
const BASE_URL = process.env.TEST_BASE_URL || "http://client:3000";

/**
 * All credentials populated during setup
 */
export interface AllCredentials {
	platform_admin: UserCredentials;
	org1_user: UserCredentials;
	org2_user: UserCredentials;
	org1: { id: string; name: string; domain: string };
	org2: { id: string; name: string; domain: string };
}

/**
 * Ensure authentication is valid for the given user.
 * This is the main entry point for self-healing auth.
 *
 * Call this in beforeAll to ensure tests have valid auth state.
 */
export async function ensureAuthenticated(
	browser: Browser,
	userKey: "platform_admin" | "org1_user" | "org2_user" = "platform_admin",
): Promise<{ context: BrowserContext; credentials: AllCredentials }> {
	const authDir = path.resolve(__dirname, "..", AUTH_STATE_DIR);
	const credentialsPath = path.resolve(__dirname, "..", getCredentialsPath());
	const authStatePath = path.resolve(
		__dirname,
		"..",
		getAuthStatePath(userKey),
	);

	// Ensure auth directory exists
	if (!fs.existsSync(authDir)) {
		fs.mkdirSync(authDir, { recursive: true });
	}

	// Try to use existing auth state
	if (fs.existsSync(authStatePath) && fs.existsSync(credentialsPath)) {
		const credentials = JSON.parse(
			fs.readFileSync(credentialsPath, "utf-8"),
		) as AllCredentials;

		// Verify the auth state is still valid
		const context = await browser.newContext({
			storageState: authStatePath,
		});
		const page = await context.newPage();

		try {
			const isValid = await verifyAuthState(page, userKey);
			if (isValid) {
				console.log(`Auth state valid for ${userKey}, reusing...`);
				await page.close();
				return { context, credentials };
			}
		} catch (e) {
			console.log(`Auth state check failed for ${userKey}: ${e}`);
		}

		await context.close();
	}

	// Auth state is missing or invalid - need to set up
	console.log(`Setting up fresh auth for ${userKey}...`);
	const credentials = await ensureUsersExist();

	// Authenticate in browser and save state
	const context = await browser.newContext();
	const page = await context.newPage();

	await authenticateInBrowser(page, credentials[userKey]);
	await context.storageState({ path: authStatePath });

	console.log(`Auth state saved for ${userKey}`);
	await page.close();

	// Return a fresh context with the saved state
	const freshContext = await browser.newContext({
		storageState: authStatePath,
	});
	return { context: freshContext, credentials };
}

/**
 * Verify that the current auth state is valid by checking if we can access
 * a protected page without being redirected to login.
 */
async function verifyAuthState(page: Page, _userKey: string): Promise<boolean> {
	try {
		await page.goto(`${BASE_URL}/`, {
			waitUntil: "domcontentloaded",
			timeout: 10000,
		});

		const mainContent = page.locator("main");
		await expect
			.poll(
				async () =>
					page.url().includes("/login") ||
					(await mainContent.isVisible()),
				{ timeout: 10000 },
			)
			.toBe(true);

		return (
			!page.url().includes("/login") && (await mainContent.isVisible())
		);
	} catch (e) {
		console.log(`verifyAuthState error: ${e}`);
		return false;
	}
}

/**
 * Ensure test users exist in the system.
 * Creates them if needed, returns existing credentials if already set up.
 */
async function ensureUsersExist(): Promise<AllCredentials> {
	const credentialsPath = path.resolve(__dirname, "..", getCredentialsPath());

	// Check system status
	const statusRes = await fetch(`${API_URL}/auth/status`);
	const _status = await statusRes.json();

	// If we have credentials file, try to use them
	if (fs.existsSync(credentialsPath)) {
		const credentials = JSON.parse(
			fs.readFileSync(credentialsPath, "utf-8"),
		) as AllCredentials;

		// Verify credentials work by trying to login
		try {
			await loginUserViaAPI(credentials.platform_admin);
			console.log("Existing credentials valid, reusing...");
			return credentials;
		} catch (e) {
			console.log(`Existing credentials invalid: ${e}`);
			// Fall through to create new users
		}
	}

	// Need to create users from scratch
	console.log("Creating test users via API...");
	const credentials = await createTestUsers();

	// Save credentials
	fs.writeFileSync(credentialsPath, JSON.stringify(credentials, null, 2));
	console.log(`Saved credentials to ${credentialsPath}`);

	return credentials;
}

/**
 * Create all test users and organizations via API.
 */
async function createTestUsers(): Promise<AllCredentials> {
	// 1. Register and authenticate platform admin (first user = superuser)
	const platformAdmin = await registerAndSetupMFA(USERS.platform_admin);
	console.log(`  Created platform admin: ${platformAdmin.email}`);

	// 2. Create organizations
	const org1 = await createOrganization(
		platformAdmin.accessToken,
		ORGANIZATIONS.org1,
	);
	console.log(`  Created org1: ${org1.name}`);

	const org2 = await createOrganization(
		platformAdmin.accessToken,
		ORGANIZATIONS.org2,
	);
	console.log(`  Created org2: ${org2.name}`);

	// 3. Create org users
	const org1User = await createOrgUser(
		platformAdmin.accessToken,
		USERS.org1_user,
		org1.id,
	);
	console.log(`  Created org1 user: ${org1User.email}`);

	const org2User = await createOrgUser(
		platformAdmin.accessToken,
		USERS.org2_user,
		org2.id,
	);
	console.log(`  Created org2 user: ${org2User.email}`);

	return {
		platform_admin: platformAdmin,
		org1_user: org1User,
		org2_user: org2User,
		org1,
		org2,
	};
}

/**
 * Register a user and complete MFA setup via API.
 */
async function registerAndSetupMFA(user: {
	email: string;
	password: string;
	name: string;
}): Promise<UserCredentials> {
	// Register
	const registerRes = await fetch(`${API_URL}/auth/register`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({
			email: user.email,
			password: user.password,
			name: user.name,
		}),
	});

	if (!registerRes.ok) {
		const error = await registerRes.text();
		throw new Error(`Register failed for ${user.email}: ${error}`);
	}

	const registerData = await registerRes.json();
	const userId = registerData.id;
	const isSuperuser = registerData.is_superuser || false;

	// Login to get MFA token
	const loginRes = await fetch(`${API_URL}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/x-www-form-urlencoded" },
		body: new URLSearchParams({
			username: user.email,
			password: user.password,
		}),
	});

	if (!loginRes.ok) {
		const error = await loginRes.text();
		throw new Error(`Login failed for ${user.email}: ${error}`);
	}

	const loginData = await loginRes.json();
	const mfaToken = loginData.mfa_token || loginData.access_token;

	// Setup MFA
	const mfaSetupRes = await fetch(`${API_URL}/auth/mfa/setup`, {
		method: "POST",
		headers: { Authorization: `Bearer ${mfaToken}` },
	});

	if (!mfaSetupRes.ok) {
		const error = await mfaSetupRes.text();
		throw new Error(`MFA setup failed for ${user.email}: ${error}`);
	}

	const mfaSetupData = await mfaSetupRes.json();
	const totpSecret = mfaSetupData.secret;

	// Verify MFA with TOTP code
	const totpCode = generateTOTP(totpSecret);
	const mfaVerifyRes = await fetch(`${API_URL}/auth/mfa/verify`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${mfaToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ code: totpCode }),
	});

	if (!mfaVerifyRes.ok) {
		const error = await mfaVerifyRes.text();
		throw new Error(`MFA verify failed for ${user.email}: ${error}`);
	}

	const tokens = await mfaVerifyRes.json();

	return {
		email: user.email,
		password: user.password,
		name: user.name,
		totpSecret,
		userId,
		accessToken: tokens.access_token,
		refreshToken: tokens.refresh_token,
		isSuperuser,
	};
}

/**
 * Create an organization via API.
 */
async function createOrganization(
	accessToken: string,
	org: { name: string; domain: string },
): Promise<{ id: string; name: string; domain: string }> {
	const response = await fetch(`${API_URL}/api/organizations`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${accessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify(org),
	});

	if (!response.ok) {
		const error = await response.text();
		throw new Error(`Create org failed for ${org.name}: ${error}`);
	}

	return response.json();
}

/**
 * Create an org user via platform admin, then complete registration.
 */
async function createOrgUser(
	adminAccessToken: string,
	user: { email: string; password: string; name: string },
	organizationId: string,
): Promise<UserCredentials> {
	// Platform admin creates user stub
	const createRes = await fetch(`${API_URL}/api/users`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${adminAccessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify({
			email: user.email,
			name: user.name,
			organization_id: organizationId,
			is_superuser: false,
		}),
	});

	if (!createRes.ok) {
		const error = await createRes.text();
		throw new Error(`Create user failed for ${user.email}: ${error}`);
	}

	// User completes registration and MFA setup
	const credentials = await registerAndSetupMFA(user);
	credentials.organizationId = organizationId;
	return credentials;
}

/**
 * Login an existing user with MFA via API.
 */
async function loginUserViaAPI(
	credentials: UserCredentials,
): Promise<UserCredentials> {
	// Login
	const loginRes = await fetch(`${API_URL}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/x-www-form-urlencoded" },
		body: new URLSearchParams({
			username: credentials.email,
			password: credentials.password,
		}),
	});

	if (!loginRes.ok) {
		const error = await loginRes.text();
		throw new Error(`Login failed for ${credentials.email}: ${error}`);
	}

	const loginData = await loginRes.json();

	// Handle MFA if required
	if (loginData.mfa_required) {
		const mfaToken = loginData.mfa_token;
		const totpCode = generateTOTP(credentials.totpSecret);

		const mfaRes = await fetch(`${API_URL}/auth/mfa/login`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ mfa_token: mfaToken, code: totpCode }),
		});

		if (!mfaRes.ok) {
			const error = await mfaRes.text();
			throw new Error(
				`MFA login failed for ${credentials.email}: ${error}`,
			);
		}

		const tokens = await mfaRes.json();
		return {
			...credentials,
			accessToken: tokens.access_token,
			refreshToken: tokens.refresh_token,
		};
	}

	return {
		...credentials,
		accessToken: loginData.access_token,
		refreshToken: loginData.refresh_token,
	};
}

/**
 * Authenticate a user in the browser (login page + MFA).
 */
async function authenticateInBrowser(
	page: Page,
	credentials: UserCredentials,
): Promise<void> {
	console.log(`Navigating to ${BASE_URL}/login...`);
	await page.goto(`${BASE_URL}/login`, { waitUntil: "domcontentloaded" });

	// Wait for login form
	await expect(page.getByLabel("Email")).toBeVisible({ timeout: 15000 });

	// Fill credentials
	await page.getByLabel("Email").fill(credentials.email);
	await page.getByLabel("Password").fill(credentials.password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();

	const mfaInput = page.getByLabel(/code|totp|verification/i);
	await expect(mfaInput).toBeVisible();
	console.log("MFA input found, entering TOTP code...");

	const totpCode = generateTOTP(credentials.totpSecret);
	await mfaInput.fill(totpCode);
	await page.getByRole("button", { name: /verify|submit|continue/i }).click();
	console.log("Clicked MFA verify button");

	// Wait for redirect away from login page
	await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
		timeout: 15000,
	});

	// Give the page a moment to settle
	await page.waitForLoadState("networkidle");

	// Verify we're logged in
	await expect(
		page.getByRole("button", { name: "Sign In", exact: true }),
	).not.toBeVisible({ timeout: 5000 });
}

/**
 * Get credentials for a specific user.
 * Returns null if credentials don't exist.
 */
export function getCredentials(): AllCredentials | null {
	const credentialsPath = path.resolve(__dirname, "..", getCredentialsPath());

	if (!fs.existsSync(credentialsPath)) {
		return null;
	}

	return JSON.parse(
		fs.readFileSync(credentialsPath, "utf-8"),
	) as AllCredentials;
}
