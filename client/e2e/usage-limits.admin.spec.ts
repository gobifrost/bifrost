import { expect, test } from "./fixtures/api-fixture";

test.describe("Usage limits", () => {
	test("edits an organization portable limit from an exact organization boundary", async ({
		page,
		api,
	}) => {
		const orgsResponse = await api.get("/api/organizations");
		expect(orgsResponse.ok()).toBe(true);
		const organizations = (await orgsResponse.json()) as Array<{
			id: string;
			name: string;
			is_provider: boolean;
		}>;
		const organization = organizations.find((candidate) => !candidate.is_provider);
		if (!organization) throw new Error("Expected a customer organization fixture");
		const boundary = `organization:${organization.id}`;

		await api.delete(
			`/api/settings/ai/usage-limits/organization/${organization.id}`,
			{ headers: { "X-Bifrost-Boundary": boundary } },
		);

		try {
			await page.goto("/reports/usage");

			const contextButton = page.getByRole("button", { name: /working in/i });
			await expect(contextButton).toBeVisible({ timeout: 10000 });
			await contextButton.click();
			await page
				.getByRole("menuitemradio", { name: organization.name })
				.click();
			await expect(
				page.getByRole("button", {
					name: `Working in ${organization.name}`,
				}),
			).toBeVisible();

			await page.getByRole("tab", { name: "Limits" }).click();
			await expect(
				page.getByText("Portable usage limits", { exact: true }),
			).toBeVisible();
			await expect(
				page.getByText(/Solution, then User, then Organization, then Platform/),
			).toBeVisible();
			await expect(page.getByText(organization.name).first()).toBeVisible();

			const modelRequestInputs =
				await page.getByLabel(/model requests/i).all();
			await expect(modelRequestInputs[0]).toBeVisible();
			await modelRequestInputs[0].fill("5");
			await page.getByRole("button", { name: "Save limit" }).click();
			await expect(page.getByText("Usage limit saved")).toBeVisible();

			await expect(
				page.getByText(/Organization supplies the per-run ceiling/),
			).toBeVisible();
			const winningPerRun = page
				.getByText("Winning per-run ceiling")
				.locator("..");
			await expect(winningPerRun.getByText("5 requests")).toBeVisible();
		} finally {
			await api.delete(
				`/api/settings/ai/usage-limits/organization/${organization.id}`,
				{ headers: { "X-Bifrost-Boundary": boundary } },
			);
		}
	});
});
