import { test, expect } from "./fixtures/api-fixture";

test("authorizes a read-only private source and installs a Solution", async ({
	page,
	api,
}) => {
	// One real install job proves the browser -> authenticated staging ->
	// durable install boundary. Backend E2E owns the later fresh-token update.
	const fixture = await api.post("/__fixture/solution-repository", {
		data: { action: "create" },
	});
	expect(fixture.ok()).toBeTruthy();
	const { slug } = await fixture.json();
	let solutionId: string | undefined;
	try {
		await page.goto("/settings/github");
		const origin = new URL(page.url()).origin;
		await page.route(
			"https://github.com/organizations/*/settings/apps/new?*",
			async (route) => {
				const state =
					new URL(route.request().url()).searchParams.get("state") ??
					"";
				const manifest = JSON.parse(
					new URLSearchParams(route.request().postData() ?? "").get(
						"manifest",
					) ?? "{}",
				);
				expect(manifest.default_permissions).toEqual({
					contents: "read",
				});
				await route.fulfill({
					status: 302,
					headers: {
						location: `${origin}/settings/github?code=fixture-manifest-${slug}&state=${encodeURIComponent(state)}`,
					},
				});
			},
		);
		await page.route(
			"https://github.com/login/oauth/authorize?*",
			async (route) => {
				const state =
					new URL(route.request().url()).searchParams.get("state") ??
					"";
				await route.fulfill({
					status: 302,
					headers: {
						location: `${origin}/settings/github?code=fixture-oauth-code&state=${encodeURIComponent(state)}`,
					},
				});
			},
		);
		await page
			.getByRole("button", { name: "Solution Repositories", exact: true })
			.click();
		await page
			.getByRole("combobox", { name: "Source Organization" })
			.click();
		await page.getByRole("option", { name: "Global", exact: true }).click();
		const setup = page.getByRole("region", { name: "GitHub App Setup" });
		await setup
			.getByLabel("GitHub Owner", { exact: true })
			.fill("thin-git-app");
		await setup
			.getByRole("button", { name: "Create GitHub App", exact: true })
			.click();
		await setup
			.getByRole("button", { name: "Authorize With GitHub" })
			.click();
		await setup
			.getByLabel("Installation", { exact: true })
			.selectOption("2718");
		await setup.getByLabel("App Repository").selectOption("1619");
		await setup
			.getByRole("button", { name: "Use Selected Repository" })
			.click();
		await expect(
			page.getByText("thin-git-app/solutions", { exact: true }),
		).toBeVisible();
		await page.goto("/solutions");
		await page
			.getByRole("button", { name: "Install Solution", exact: true })
			.click();
		await page
			.getByRole("button", { name: /Import as a Solution/i })
			.click();
		await page.getByRole("button", { name: /From a repository/i }).click();
		await page
			.getByRole("combobox", { name: "Install Organization" })
			.click();
		await page.getByRole("option", { name: "Global", exact: true }).click();
		await page
			.getByRole("combobox", { name: "Repository Access" })
			.selectOption({
				label: "Bifrost Fixture App · thin-git-app/solutions",
			});
		await page
			.getByRole("button", { name: "Resolve", exact: true })
			.click();
		await expect(page.getByText(slug, { exact: true })).toBeVisible();
		await page
			.getByRole("button", { name: "Install", exact: true })
			.click();
		await expect(
			page.getByRole("heading", {
				name: "Private Browser Fixture",
				exact: true,
			}),
		).toBeVisible();
		solutionId = page.url().split("/solutions/")[1]?.split(/[/?#]/)[0];
		expect(solutionId).toBeTruthy();
		await expect(
			page.getByText("v1.0.0", { exact: false }).first(),
		).toBeVisible();
		expect(await page.locator("body").innerText()).not.toContain(
			"fixture-secret",
		);
	} finally {
		if (!solutionId) {
			const response = await api.get("/api/solutions");
			solutionId = (await response.json()).solutions.find(
				(solution: { slug: string }) => solution.slug === slug,
			)?.id;
		}
		if (solutionId)
			expect(
				(await api.delete(`/api/solutions/${solutionId}`)).ok(),
			).toBeTruthy();
		expect(
			(
				await api.post("/__fixture/solution-repository", {
					data: { action: "delete", slug },
				})
			).ok(),
		).toBeTruthy();
	}
});
