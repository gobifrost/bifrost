import { randomUUID } from "node:crypto";
import type { BrowserContext, Page } from "./fixtures/api-fixture";
import { test, expect } from "./fixtures/api-fixture";
import { generateTOTP } from "./setup/totp";

const baseURL = process.env.TEST_BASE_URL || "http://localhost:3000";

async function passwordLogin(page: Page, email: string, password: string) {
	await page.goto("/login");
	await page.getByLabel("Email", { exact: true }).fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();
}

test.describe("Isolated account onboarding", () => {
	let context: BrowserContext | undefined;
	let userId = "";
	let email = "";
	let password = "";

	test.beforeEach(async ({ browser }) => {
		const id = randomUUID();
		email = `e2e-onboarding-${id}@gobifrost.dev`;
		password = `Onboarding-${id}!Aa1`;
		context = await browser.newContext({
			baseURL,
			storageState: { cookies: [], origins: [] },
		});
		const response = await context.request.post("/auth/register", {
			data: { email, password, name: "Onboarding acceptance" },
		});
		expect(response.ok(), "register isolated account").toBe(true);
		userId = ((await response.json()) as { id: string }).id;
	});

	test.afterEach(async ({ api }) => {
		await context?.close();
		context = undefined;
		if (userId) {
			const response = await api.delete(`/api/users/${userId}`);
			expect([204, 404], "delete owned onboarding account").toContain(
				response.status(),
			);
			userId = "";
		}
	});

	test("SECURITY-02 enrolls TOTP, downloads recovery codes, and signs in using a recovery code", async ({
		browser,
	}) => {
		let page = await context!.newPage();
		const setupResponse = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === "/auth/mfa/setup" &&
				r.request().method() === "POST",
		);
		await passwordLogin(page, email, password);
		await expect(page).toHaveURL(/\/mfa-setup$/);
		const setup = await setupResponse;
		expect(setup.ok(), "initialize TOTP").toBe(true);
		const { secret } = (await setup.json()) as { secret: string };
		await page.getByRole("button", { name: "I've added the code" }).click();
		await page
			.getByLabel("Verification Code", { exact: true })
			.fill(generateTOTP(secret));
		const verifiedResponse = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === "/auth/mfa/verify" &&
				r.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Verify", exact: true }).click();
		const verified = await verifiedResponse;
		expect(verified.ok(), "verify TOTP enrollment").toBe(true);
		const { recovery_codes: codes } = (await verified.json()) as {
			recovery_codes: string[];
		};
		expect(codes.length > 0, "enrollment generated recovery codes").toBe(
			true,
		);
		await expect(
			page.getByRole("heading", { name: "Save your recovery codes" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Continue to Login" }),
		).toBeDisabled();
		const downloaded = page.waitForEvent("download");
		await page
			.getByRole("button", { name: "Download", exact: true })
			.click();
		const download = await downloaded;
		expect(download.suggestedFilename()).toBe("bifrost-recovery-codes.txt");
		const stream = await download.createReadStream();
		const chunks: Buffer[] = [];
		for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
		const text = Buffer.concat(chunks).toString("utf8");
		expect(
			codes.every((code) => text.includes(code)),
			"download contains all issued codes",
		).toBe(true);
		await page.getByLabel("I have saved my recovery codes").check();
		await page.getByRole("button", { name: "Continue to Login" }).click();
		// Enrollment issues a session. Prove recovery sign-in in an independent browser context.
		await context!.close();
		context = await browser.newContext({
			baseURL,
			storageState: { cookies: [], origins: [] },
		});
		page = await context.newPage();
		await passwordLogin(page, email, password);
		await page
			.getByLabel("Authentication Code", { exact: true })
			.fill(codes[0]);
		await page.getByRole("button", { name: "Verify", exact: true }).click();
		await page.waitForURL(
			(u) => !["/login", "/mfa-setup"].includes(u.pathname),
		);
		const profile = await context!.request.get("/api/profile");
		expect(
			profile.ok(),
			"recovery-code sign-in established authenticated profile",
		).toBe(true);
		expect(((await profile.json()) as { email: string }).email).toBe(email);
	});

	test("SECURITY-03 changes a password in settings and signs in with the saved password", async ({
		browser,
	}) => {
		const login = await context!.request.post("/auth/login", {
			form: { username: email, password },
		});
		expect(login.ok(), "request MFA setup capability").toBe(true);
		const { mfa_token } = (await login.json()) as { mfa_token: string };
		const headers = { Authorization: `Bearer ${mfa_token}` };
		const setup = await context!.request.post("/auth/mfa/setup", {
			headers,
		});
		expect(setup.ok(), "seed enrolled authenticator").toBe(true);
		const { secret } = (await setup.json()) as { secret: string };
		const verified = await context!.request.post("/auth/mfa/verify", {
			headers,
			data: { code: generateTOTP(secret) },
		});
		expect(verified.ok(), "persist seeded authenticator").toBe(true);
		const page = await context!.newPage();
		await page.goto("/user-settings/basic-info");
		const changedPassword = `Changed-${randomUUID()}!Aa1`;
		await page
			.getByLabel("Current Password", { exact: true })
			.fill(password);
		await page
			.getByLabel("New Password", { exact: true })
			.fill(changedPassword);
		await page
			.getByLabel("Confirm Password", { exact: true })
			.fill(changedPassword);
		const savedResponse = page.waitForResponse(
			(r) =>
				new URL(r.url()).pathname === "/api/profile/password" &&
				r.request().method() === "POST",
		);
		await page
			.getByRole("button", { name: "Change Password", exact: true })
			.click();
		expect((await savedResponse).ok(), "persist password change").toBe(
			true,
		);
		await page.reload();
		await expect(
			page.getByLabel("Current Password", { exact: true }),
		).toBeVisible();
		const fresh = await browser.newContext({
			baseURL,
			storageState: { cookies: [], origins: [] },
		});
		try {
			const freshPage = await fresh.newPage();
			await passwordLogin(freshPage, email, changedPassword);
			await freshPage
				.getByLabel("Authentication Code", { exact: true })
				.fill(generateTOTP(secret));
			await freshPage
				.getByRole("button", { name: "Verify", exact: true })
				.click();
			await freshPage.waitForURL((u) => u.pathname !== "/login");
			const profile = await fresh.request.get("/api/profile");
			expect(profile.ok(), "new password signs in independently").toBe(
				true,
			);
			expect(((await profile.json()) as { email: string }).email).toBe(
				email,
			);
		} finally {
			await fresh.close();
		}
	});
});
