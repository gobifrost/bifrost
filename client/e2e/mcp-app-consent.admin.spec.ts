import { createHash, randomBytes } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("reviews an MCP app before approving its own-account connection", async ({
	page,
	request,
}, testInfo) => {
	const verifier = randomBytes(32).toString("base64url");
	const challenge = createHash("sha256").update(verifier).digest("base64url");
	const redirectUri = "https://client.example/callback?existing=value";
	let clientId = "";
	let stateId = "";
	async function openConnection(clientName?: string) {
		const registration = await request.post("/register", {
			data: {
				redirect_uris: [redirectUri],
				...(clientName ? { client_name: clientName } : {}),
			},
		});
		expect(registration.ok()).toBeTruthy();
		clientId = (await registration.json()).client_id;
		const authorization = await request.get("/authorize", {
			params: {
				response_type: "code",
				client_id: clientId,
				redirect_uri: redirectUri,
				state: "client-state",
				code_challenge: challenge,
				code_challenge_method: "S256",
			},
			maxRedirects: 0,
		});
		expect(authorization.status()).toBe(302);
		const login = new URL(authorization.headers().location);
		const consent = new URL(login.searchParams.get("return_to")!);
		stateId = consent.searchParams.get("request")!;
		await page.goto(consent.pathname + consent.search);
	}
	await openConnection("Example Assistant");
	await expect(
		page.getByRole("heading", {
			name: "Allow Example Assistant to Access Bifrost?",
		}),
	).toBeVisible();
	await page.screenshot({
		path: testInfo.outputPath("mcp-named-app.png"),
		fullPage: true,
	});
	await page.setViewportSize({ width: 390, height: 844 });
	await openConnection();
	await expect(
		page.getByRole("heading", {
			name: "Allow Unnamed App to Access Bifrost?",
		}),
	).toBeVisible();
	await expect(
		page.getByText("This app did not provide a name."),
	).toBeVisible();
	await expect(page.getByText(redirectUri, { exact: true })).toBeVisible();
	const preview = await page.request.get(`/auth/app-connections/${stateId}`);
	expect(preview.ok()).toBeTruthy();
	await page.screenshot({
		path: testInfo.outputPath("mcp-unnamed-app.png"),
		fullPage: true,
	});
	await page.route("https://client.example/callback**", (route) =>
		route.fulfill({
			contentType: "text/html",
			body: "<h1>Client Connected</h1>",
		}),
	);
	await page.getByRole("button", { name: "Allow", exact: true }).click();
	await expect(page).toHaveURL(/https:\/\/client\.example\/callback\?/);
	const callback = new URL(page.url());
	expect(callback.searchParams.get("existing")).toBe("value");
	expect(callback.searchParams.get("state")).toBe("client-state");
	const tokens = await request.post("/token", {
		form: {
			grant_type: "authorization_code",
			client_id: clientId,
			redirect_uri: redirectUri,
			code: callback.searchParams.get("code")!,
			code_verifier: verifier,
		},
	});
	expect(tokens.ok()).toBeTruthy();
	expect((await tokens.json()).access_token).toBeTruthy();
});
