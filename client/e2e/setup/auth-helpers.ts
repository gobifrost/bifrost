/* eslint-disable no-console */
/**
 * Auth Helpers for E2E Testing
 *
 * Provides utilities for user registration, MFA setup, and authentication.
 * Mirrors the backend E2E fixtures in api/tests/e2e/fixtures/setup.py.
 */

import { Page, expect } from "@playwright/test";
import * as path from "path";
import { fileURLToPath } from "url";
import { generateTOTP } from "./totp";
import {
	USERS,
	ORGANIZATIONS,
	type TestUser,
	type UserCredentials,
} from "../fixtures/users";

// ESM equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// API URL from environment - defaults to Docker internal network
const API_URL = process.env.TEST_API_URL || "http://api:8000";

// Results directory - use relative path for local, /app for Docker
const RESULTS_DIR = process.env.CI
	? "/app/playwright-results"
	: path.resolve(__dirname, "../../playwright-results");

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
 * Create all test users and organizations via API.
 *
 * This function:
 * 1. Registers platform_admin (first user = superuser)
 * 2. Sets up MFA for platform_admin
 * 3. Creates org1 and org2
 * 4. Creates org1_user and org2_user
 * 5. Sets up MFA for each org user
 *
 * @returns All user credentials and org info
 */
export async function createTestUsers(): Promise<AllCredentials> {
	console.log("Creating test users via API...");

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
async function registerAndSetupMFA(user: TestUser): Promise<UserCredentials> {
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
	user: TestUser,
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
 * Authenticate a user in the browser (login page + MFA).
 *
 * This function navigates to the login page, fills credentials,
 * handles MFA if required, and waits for successful login.
 *
 * @param page - Playwright page instance
 * @param credentials - User credentials including TOTP secret
 */
export async function authenticateInBrowser(
	page: Page,
	credentials: UserCredentials,
): Promise<void> {
	const baseURL = process.env.TEST_BASE_URL || "http://client:3000";

	console.log(`Navigating to ${baseURL}/login...`);
	const response = await page.goto(`${baseURL}/login`, {
		waitUntil: "domcontentloaded",
	});
	console.log(`Navigation response status: ${response?.status()}`);

	// The form becoming visible is the readiness contract; a fixed delay only
	// hides rendering and routing regressions.
	await expect(page.getByLabel("Email")).toBeVisible({ timeout: 15000 });

	// Debug: capture what's on the page
	console.log(`Page title: ${await page.title()}`);
	console.log(`Page URL: ${page.url()}`);
	const bodyText = await page.locator("body").textContent();
	console.log(`Page body (first 500 chars): ${bodyText?.substring(0, 500)}`);

	// Take screenshot for debugging
	const screenshotPath = path.join(RESULTS_DIR, "login-debug.png");
	await page.screenshot({ path: screenshotPath });
	console.log(`Screenshot saved to ${screenshotPath}`);

	// Fill credentials
	await page.getByLabel("Email").fill(credentials.email);
	await page.getByLabel("Password").fill(credentials.password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();

	const mfaInput = page.getByLabel(/code|totp|verification/i);
	await expect(mfaInput).toBeVisible();

	// Debug: Check for error messages after login attempt
	const bodyTextAfterLogin = await page.locator("body").textContent();
	console.log(
		`Page body after login click (first 500 chars): ${bodyTextAfterLogin?.substring(0, 500)}`,
	);
	console.log(`Current URL after login click: ${page.url()}`);

	// Check for error messages (alert, toast, or inline)
	const errorAlert = page.getByRole("alert");
	const errorText = page.getByText(
		/error|invalid|failed|too many|rate limit/i,
	);
	const hasError = await errorAlert
		.or(errorText)
		.first()
		.isVisible()
		.catch(() => false);
	if (hasError) {
		const errorContent = await errorAlert
			.or(errorText)
			.first()
			.textContent()
			.catch(() => "unknown error");
		console.log(`Login error detected: ${errorContent}`);
	}

	console.log("MFA input found, entering TOTP code...");
	const totpCode = generateTOTP(credentials.totpSecret);
	console.log(`Generated TOTP code: ${totpCode.substring(0, 3)}***`);
	await mfaInput.fill(totpCode);
	await page.getByRole("button", { name: /verify|submit|continue/i }).click();
	console.log("Clicked MFA verify button");

	// Wait for redirect away from login page (authenticated)
	// Could be / (dashboard), /forms (org users), or other authenticated routes
	console.log(
		`Waiting for redirect from login page (current URL: ${page.url()})...`,
	);
	await page.waitForURL((url) => !url.pathname.startsWith("/login"), {
		timeout: 15000,
	});

	// Give the page a moment to settle after navigation
	await page.waitForLoadState("networkidle");

	// Verify we're logged in (Sign In button should not be visible)
	await expect(
		page.getByRole("button", { name: "Sign In", exact: true }),
	).not.toBeVisible({ timeout: 5000 });
}

/**
 * Login an existing user with MFA via API (refresh tokens).
 */
export async function loginUserViaAPI(
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
