import { expect, test } from "@playwright/test";

/**
 * Catalog happy path against the real API: the provider list and model list
 * come from the models.dev catalog the platform serves. Only the live
 * credential check is mocked, because it would call the real provider.
 */
test("adds a catalog provider and a profile with a reasoning choice", async ({
	page,
}) => {
	const suffix = Date.now().toString(36);
	await page.route("**/api/admin/ai/connections/verify", async (route) => {
		await route.fulfill({ json: { success: true, message: "Connected", models: [] } });
	});

	await page.goto("/settings/ai");
	await expect(page.getByTestId("model-catalog-status")).toContainText(
		"providers from the models.dev catalog",
	);

	await page.getByRole("button", { name: "Add Provider" }).first().click();
	const providerDialog = page.getByRole("dialog");
	await providerDialog.getByRole("combobox", { name: "Provider" }).click();
	await page.getByPlaceholder("Search providers...").fill("Anthropic");
	await page.getByRole("option", { name: /^Anthropic https:\/\/api\.anthropic\.com/ }).click();
	await expect(providerDialog.getByLabel("Endpoint")).toHaveValue("https://api.anthropic.com");
	await providerDialog.getByLabel("Connection Name").fill(`Anthropic ${suffix}`);
	await providerDialog.getByLabel("API Key").fill("sk-test");
	await providerDialog.getByRole("button", { name: "Add Provider" }).click();
	await expect(page.getByRole("button", { name: `Edit Anthropic ${suffix}` })).toBeVisible();

	await page.getByRole("button", { name: "Add Profile" }).first().click();
	const profileDialog = page.getByRole("dialog");
	await profileDialog.getByLabel("Profile Name").fill(`Haiku ${suffix}`);
	await profileDialog.getByLabel("Provider Connection").click();
	await page.getByRole("option", { name: new RegExp(`Anthropic ${suffix}`) }).click();
	await profileDialog.getByLabel("Model", { exact: true }).click();
	await page.getByPlaceholder("Search models...").fill("claude-haiku-4-5");
	await page.getByRole("option", { name: /Claude Haiku 4\.5.*reasoning/ }).first().click();
	await profileDialog.getByRole("combobox", { name: "Reasoning" }).click();
	await page.getByRole("option", { name: "On (provider default)", exact: true }).click();
	await profileDialog.getByRole("button", { name: "Add Profile", exact: true }).click();

	const profileCard = page.locator('[data-slot="card"]', { hasText: `Haiku ${suffix}` });
	await expect(profileCard.getByText("Reasoning: On (provider default)")).toBeVisible();
});
