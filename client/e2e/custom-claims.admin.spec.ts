import { test, expect } from "@playwright/test";
import * as fs from "fs";
import * as path from "path";
import { fileURLToPath } from "url";

import { authenticateInBrowser } from "./setup/auth-helpers";
import { generateTOTP } from "./setup/totp";
import { getCredentialsPath, type UserCredentials } from "./fixtures/users";

const API_URL = process.env.TEST_API_URL || "http://api:8000";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

test.setTimeout(90_000);

interface CredentialsFile {
	platform_admin: UserCredentials;
	org1: { id: string; name: string; domain: string };
}

function loadCredentials(): CredentialsFile {
	const credentialsPath = path.resolve(__dirname, getCredentialsPath());
	return JSON.parse(fs.readFileSync(credentialsPath, "utf-8"));
}

async function assertOk(response: Response, label: string): Promise<void> {
	if (!response.ok) {
		throw new Error(`${label}: ${await response.text()}`);
	}
}

async function createOrgSuperuser(): Promise<UserCredentials> {
	const credentials = loadCredentials();
	const suffix = `${Date.now()}_${Math.floor(Math.random() * 10000)}`;
	const user = {
		email: `claims_admin_${suffix}@gobifrost.dev`,
		password: "ClaimsAdminPass123!",
		name: `Claims Admin ${suffix}`,
	};

	const createRes = await fetch(`${API_URL}/api/users`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${credentials.platform_admin.accessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify({
			email: user.email,
			name: user.name,
			organization_id: credentials.org1.id,
			is_superuser: true,
		}),
	});
	await assertOk(createRes, "create org superuser");

	const registerRes = await fetch(`${API_URL}/auth/register`, {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(user),
	});
	await assertOk(registerRes, "register org superuser");
	const registerData = await registerRes.json();

	const loginRes = await fetch(`${API_URL}/auth/login`, {
		method: "POST",
		headers: { "Content-Type": "application/x-www-form-urlencoded" },
		body: new URLSearchParams({
			username: user.email,
			password: user.password,
		}),
	});
	await assertOk(loginRes, "login org superuser");
	const loginData = await loginRes.json();
	const mfaToken = loginData.mfa_token || loginData.access_token;

	const mfaSetupRes = await fetch(`${API_URL}/auth/mfa/setup`, {
		method: "POST",
		headers: { Authorization: `Bearer ${mfaToken}` },
	});
	await assertOk(mfaSetupRes, "set up org superuser MFA");
	const totpSecret = (await mfaSetupRes.json()).secret;

	const mfaVerifyRes = await fetch(`${API_URL}/auth/mfa/verify`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${mfaToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify({ code: generateTOTP(totpSecret) }),
	});
	await assertOk(mfaVerifyRes, "verify org superuser MFA");
	const tokens = await mfaVerifyRes.json();

	return {
		email: user.email,
		password: user.password,
		name: user.name,
		totpSecret,
		userId: registerData.id,
		organizationId: credentials.org1.id,
		accessToken: tokens.access_token,
		refreshToken: tokens.refresh_token,
		isSuperuser: true,
	};
}

async function createSourceTable(admin: UserCredentials): Promise<string> {
	const tableName = `ui_claim_memberships_${Date.now()}_${Math.floor(
		Math.random() * 10000,
	)}`;
	const response = await fetch(`${API_URL}/api/tables`, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${admin.accessToken}`,
			"Content-Type": "application/json",
		},
		body: JSON.stringify({
			name: tableName,
			description: "Custom Claims Playwright source table",
			organization_id: admin.organizationId,
		}),
	});
	await assertOk(response, "create source table");
	return tableName;
}

test("admin creates a Custom Claim from the Tables page", async ({ page }) => {
	const admin = await createOrgSuperuser();
	const sourceTable = await createSourceTable(admin);
	const claimName = `allowed_campus_ids_${Date.now()}`;
	const query = JSON.stringify(
		{ table: sourceTable, select: "campus_id" },
		null,
		2,
	);

	await page.context().clearCookies();
	await page.goto("/login");
	await page.evaluate(() => localStorage.clear());
	await authenticateInBrowser(page, admin);

	await page.goto("/tables");
	await page.getByRole("tab", { name: /custom claims/i }).click();
	await page.getByRole("button", { name: /add claim/i }).click();
	await page.getByLabel("Name").fill(claimName);

	const editor = page.getByTestId("json-yaml-editor-json");
	await editor.locator("textarea").first().waitFor({ timeout: 20_000 });
	await page.evaluate((nextQuery) => {
		const monaco = (
			window as typeof window & {
				monaco?: {
					editor?: {
						getModels?: () => Array<{
							uri: { path: string };
							setValue: (value: string) => void;
						}>;
					};
				};
			}
		).monaco;
		const model = monaco?.editor
			?.getModels?.()
			.find((item) => item.uri.path.endsWith("claim-query.json"));
		if (!model) throw new Error("claim-query.json Monaco model not found");
		model.setValue(nextQuery);
	}, query);

	const save = page.getByRole("button", { name: /save/i });
	await expect(save).toBeEnabled();
	await save.click();

	await expect(page.getByText(claimName)).toBeVisible({ timeout: 10_000 });
	await expect(page.getByText(sourceTable)).toBeVisible();
});
