import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("an admin adds, restricts, disables and deletes an Identity's federated credential", async ({
	api,
	page,
}, testInfo) => {
	await page.setViewportSize({ width: 1440, height: 1000 });
	const created = await api.post("/api/identities", {
		data: {
			name: `Federation ${randomUUID().slice(0, 8)}`,
			organization_id: null,
		},
	});
	expect(created.ok()).toBe(true);
	const identity = await created.json();
	try {
		await page.goto(`/users/${identity.id}`);
		await page
			.getByRole("button", { name: "New Federated Credential" })
			.click();
		const dialog = page.getByRole("dialog", {
			name: "New Federated Credential",
		});
		await dialog.getByLabel("Name", { exact: true }).fill("Deployment Job");
		await dialog
			.getByLabel("Issuer URL")
			.fill("https://token.actions.githubusercontent.com");
		await dialog
			.getByLabel("Audience", { exact: true })
			.fill("https://bifrost.example");
		await dialog
			.getByLabel("Subject", { exact: true })
			.fill("repo:example/repo:environment:production");
		await dialog.getByRole("button", { name: "Add Claim" }).click();
		await dialog.getByLabel("Claim Name 1").fill("repository_id");
		await dialog.getByLabel("Exact Value 1").fill("12345");
		await dialog
			.getByLabel("Name", { exact: true })
			.scrollIntoViewIfNeeded();
		await dialog.screenshot({
			path: testInfo.outputPath("federated-credential-form.png"),
		});
		await dialog.getByRole("button", { name: "Save Credential" }).click();
		await expect(dialog).toBeHidden();
		const section = page.getByRole("region", {
			name: "Federated Credentials",
		});
		await expect(
			section.getByText("Deployment Job", { exact: true }),
		).toBeVisible();
		await expect(section.getByText("12345", { exact: true })).toBeVisible();
		await section.scrollIntoViewIfNeeded();
		await page.screenshot({
			path: testInfo.outputPath("federated-credential-configured.png"),
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await section.screenshot({
			path: testInfo.outputPath("federated-credential-phone.png"),
		});
		await section
			.getByRole("button", { name: "Disable Deployment Job" })
			.click();
		await expect(
			section.getByText("Disabled", { exact: true }),
		).toBeVisible();
		await section
			.getByRole("button", { name: "Delete Deployment Job" })
			.click();
		await page
			.getByRole("dialog", { name: "Delete Federated Credential" })
			.getByRole("button", { name: "Delete Credential", exact: true })
			.click();
		await expect(
			section.getByText("No federated credentials.", { exact: false }),
		).toBeVisible();
	} finally {
		const deleted = await api.delete(`/api/identities/${identity.id}`);
		expect([204, 404]).toContain(deleted.status());
	}
});
