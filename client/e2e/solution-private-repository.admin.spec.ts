import { test, expect } from "./fixtures/api-fixture";

test("installs a Solution from a read-only private source", async ({
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
	let sourceId: string | undefined;
	try {
		// Browser consent is covered by github-app-git.admin.spec.ts; source
		// authorization and scope refusals are covered by backend E2E. Prepare
		// this source through the real API so this journey owns only the new
		// private-repository picker -> staged bytes -> install boundary.
		const manifestResponse = await api.post("/api/github/app/manifest", {
			data: {
				owner: "thin-git-app",
				name: "Solution Fixture App",
				purpose: "solution",
				organization_id: null,
			},
		});
		expect(manifestResponse.ok()).toBeTruthy();
		const manifest = await manifestResponse.json();
		expect(manifest.manifest.default_permissions).toEqual({
			contents: "read",
		});
		const registrationState = new URL(
			manifest.registration_url,
		).searchParams.get("state");
		expect(
			(
				await api.post("/api/github/app/manifest/complete", {
					data: {
						code: `fixture-manifest-${slug}`,
						state: registrationState,
					},
				})
			).ok(),
		).toBeTruthy();
		const authorize = await api.post("/api/github/app/authorize", {
			data: { setup_id: manifest.setup_id },
		});
		expect(authorize.ok()).toBeTruthy();
		const authorizationState = new URL(
			(await authorize.json()).authorization_url,
		).searchParams.get("state");
		expect(
			(
				await api.post("/api/github/app/authorize/complete", {
					data: {
						code: "fixture-oauth-code",
						state: authorizationState,
					},
				})
			).ok(),
		).toBeTruthy();
		const select = await api.post("/api/github/app/select", {
			data: {
				setup_id: manifest.setup_id,
				installation_id: 2718,
				repository_id: 1619,
			},
		});
		expect(select.ok()).toBeTruthy();
		sourceId = (await select.json()).source_id;
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
		await page.getByRole("option", { name: /^Global\b/ }).click();
		await page
			.getByRole("combobox", { name: "Repository Access" })
			.selectOption({
				label: "Bifrost Fixture App · thin-git-app/solutions",
			});
		const resolved = page.waitForResponse(
			(response) =>
				new URL(response.url()).pathname ===
					"/api/solutions/install/preview-repo" &&
				response.request().method() === "POST",
		);
		await page
			.getByRole("button", { name: "Resolve", exact: true })
			.click();
		const previewResponse = await resolved;
		expect(previewResponse.status(), await previewResponse.text()).toBe(200);
		await expect(
			page.getByText(`(${slug})`, { exact: true }),
		).toBeVisible();
		// Observe the UI's existing status transport; do not start a second job
		// or assert the detail page while deployment is still running.
		const deployed = page.waitForResponse(async (response) => {
			if (
				!response.url().includes("/api/solutions/deploy-jobs/") ||
				!response.ok()
			)
				return false;
			const job = await response.json();
			if (job.status === "failed")
				throw new Error(job.error || "Solution installation failed");
			return job.status === "succeeded";
		});
		await page
			.getByRole("button", { name: "Install", exact: true })
			.click();
		await deployed;
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
			expect
				.soft(
					(
						await api.delete(`/api/solutions/${solutionId}`, {
							params: { confirm: slug },
						})
					).ok(),
				)
				.toBeTruthy();
		if (sourceId)
			expect
				.soft(
					(
						await api.delete(
							`/api/github/app/solution-sources/${sourceId}`,
							{ params: { organization_id: "GLOBAL" } },
						)
					).ok(),
				)
				.toBeTruthy();
		expect
			.soft(
				(
					await api.post("/__fixture/solution-repository", {
						data: { action: "delete", slug },
					})
				).ok(),
			)
			.toBeTruthy();
	}
});
