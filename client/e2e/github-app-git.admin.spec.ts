import { test, expect, waitForPlatformJob } from "./fixtures/api-fixture";

test("creates an App, authorizes it and connects the workspace", async ({
	page,
	api,
}) => {
	// Exactly one workspace.git job proves the existing UI-to-worker connection.
	// The provider fixture supplies synthetic credentials; only GitHub's browser
	// consent pages are replaced here. Bifrost API calls use live services.
	const settingsResponse = await page.goto("/settings/github");
	expect(settingsResponse?.headers()["cache-control"]).toContain("no-store");
	expect(settingsResponse?.headers()["referrer-policy"]).toBe("no-referrer");
	const origin = new URL(page.url()).origin;
	await page.route(
		"https://github.com/organizations/*/settings/apps/new?*",
		async (route) => {
			const url = new URL(route.request().url());
			const manifest = JSON.parse(
				new URLSearchParams(route.request().postData() ?? "").get(
					"manifest",
				) ?? "{}",
			);
			expect(manifest.default_permissions).toEqual({ contents: "write" });
			expect(manifest.hook_attributes.active).toBe(false);
			await route.fulfill({
				status: 302,
				headers: {
					location: `${origin}/settings/github?code=fixture-manifest-code&state=${encodeURIComponent(url.searchParams.get("state") ?? "")}`,
				},
			});
		},
	);
	await page.route(
		"https://github.com/login/oauth/authorize?*",
		async (route) => {
			const state = new URL(route.request().url()).searchParams.get(
				"state",
			);
			await route.fulfill({
				status: 302,
				headers: {
					location: `${origin}/settings/github?code=fixture-oauth-code&state=${encodeURIComponent(state ?? "")}`,
				},
			});
		},
	);
	try {
		const setup = page.getByRole("region", { name: "GitHub App Setup" });
		await setup
			.getByLabel("GitHub Owner", { exact: true })
			.fill("thin-git-app");
		await setup
			.getByRole("button", { name: "Create GitHub App", exact: true })
			.click();
		await expect(setup.getByRole("status")).toContainText(
			"Authorization Required",
		);
		expect(page.url()).not.toContain("code=");
		await setup
			.getByRole("button", { name: "Authorize With GitHub" })
			.click();
		await setup
			.getByLabel("Installation", { exact: true })
			.selectOption("2718");
		await setup.getByLabel("App Repository").selectOption("1618");
		await setup
			.getByRole("button", { name: "Use Selected Repository" })
			.click();
		await page
			.getByRole("button", { name: "Review connection", exact: true })
			.click();
		const review = page.getByRole("region", {
			name: "Review workspace connection",
		});
		await review.getByRole("radio", { name: /publish local/i }).click();
		const queued = page.waitForResponse(
			(response) =>
				response.url().endsWith("/api/github/connect") &&
				response.request().method() === "POST",
		);
		await review
			.getByRole("button", { name: "Connect GitHub", exact: true })
			.click();
		const operation = await (await queued).json();
		await waitForPlatformJob(api, operation.job_id);
		await page.reload();
		await expect(
			page.getByText("Git sync uses your GitHub App installation."),
		).toBeVisible();
		await expect(
			page.getByRole("region", { name: "Connected GitHub repository" }),
		).toContainText("thin-git-app/workspace");
		await expect(page.locator("body")).not.toContainText("fixture-secret");
	} finally {
		await api.post("/api/github/disconnect");
	}
});
