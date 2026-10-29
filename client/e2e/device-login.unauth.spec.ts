import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { UserCredentials } from "./fixtures/users";
import { generateTOTP } from "./setup/totp";

test("connects the CLI only after sign-in and explicit approval", async ({
	page,
	request,
}, testInfo) => {
	const response = await request.post("/auth/device/code");
	expect(response.ok()).toBeTruthy();
	const code = await response.json();
	await page.goto(`/device?user_code=${encodeURIComponent(code.user_code)}`);
	await expect(page.getByLabel("Email")).toBeVisible();
	const credentials: Record<string, UserCredentials> = JSON.parse(
		readFileSync(
			new URL("./.auth/credentials.json", import.meta.url),
			"utf8",
		),
	);
	const account = credentials.platform_admin;
	await page.getByLabel("Email").fill(account.email);
	await page.getByLabel("Password").fill(account.password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();
	const mfaInput = page.getByLabel(/code|totp|verification/i);
	await expect(mfaInput).toBeVisible();
	await mfaInput.fill(generateTOTP(account.totpSecret));
	await page.getByRole("button", { name: /verify|submit|continue/i }).click();
	await expect(
		page.getByRole("heading", { name: "Connect Bifrost CLI" }),
	).toBeVisible();
	await expect(page.getByText(code.user_code, { exact: true })).toBeVisible();
	await expect(page.getByText(account.email, { exact: true })).toBeVisible();
	const pending = await request.post("/auth/device/token", {
		data: { device_code: code.device_code },
	});
	expect(await pending.json()).toEqual({ error: "authorization_pending" });
	await page.screenshot({
		path: testInfo.outputPath("cli-confirmation.png"),
		fullPage: true,
	});
	await page
		.getByRole("button", { name: "Connect CLI", exact: true })
		.click();
	await expect(
		page.getByRole("heading", { name: "CLI Connected", exact: true }),
	).toBeVisible();
	await page.screenshot({
		path: testInfo.outputPath("cli-connected.png"),
		fullPage: true,
	});
	const tokenResponse = await request.post("/auth/device/token", {
		data: { device_code: code.device_code },
	});
	expect(tokenResponse.ok()).toBeTruthy();
	const token = await tokenResponse.json();
	expect(token.access_token).toBeTruthy();
	const identity = await request.get("/auth/me", {
		headers: { Authorization: `Bearer ${token.access_token}` },
	});
	expect(identity.ok()).toBeTruthy();
	expect((await identity.json()).email).toBe(account.email);
});
