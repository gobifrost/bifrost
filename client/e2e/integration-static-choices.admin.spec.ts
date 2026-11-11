import { test, expect } from "./fixtures/api-fixture";

test("an integration author defines choices that consumers use for defaults", async ({
	page,
	api,
}) => {
	const name = `Static choices ${Date.now()}`;
	let id: string | undefined;
	try {
		await page.goto("/integrations");
		await page
			.getByRole("button", { name: "Create integration", exact: true })
			.first()
			.click();
		const dialog = page.getByRole("dialog", {
			name: "Create Integration",
			exact: true,
		});
		await dialog
			.getByLabel("Integration Name", { exact: false })
			.fill(name);
		await dialog
			.getByRole("button", { name: "Add Field", exact: true })
			.click();
		await dialog.getByLabel("Field key", { exact: true }).fill("region");
		await dialog
			.getByRole("button", { name: "Add Option", exact: true })
			.click();
		await dialog
			.getByLabel("Option 1 label", { exact: true })
			.fill("Europe");
		await dialog.getByLabel("Option 1 value", { exact: true }).fill("eu");
		const created = page.waitForResponse(
			(r) =>
				r.request().method() === "POST" &&
				new URL(r.url()).pathname === "/api/integrations",
		);
		await dialog
			.getByRole("button", { name: "Create Integration", exact: true })
			.click();
		const response = await created;
		expect(response.ok()).toBe(true);
		id = (await response.json()).id;
		const stored = await api.get(`/api/integrations/${id}`);
		expect(stored.ok()).toBe(true);
		const schema = (await stored.json()).config_schema[0];
		expect(schema.options).toEqual(["eu"]);
		expect(schema.option_labels).toEqual({ eu: "Europe" });
		await page.goto(`/integrations/${id}`);
		await page
			.getByRole("button", { name: "Configure default values" })
			.click();
		const defaults = page.getByRole("dialog", {
			name: "Edit Configuration Defaults",
		});
		await defaults.getByRole("combobox", { name: /region/ }).click();
		await page.getByRole("option", { name: "Europe", exact: true }).click();
		await defaults.getByRole("button", { name: "Save Defaults" }).click();
		await expect(defaults).toBeHidden();
		await page.reload();
		await page
			.getByRole("button", { name: "Configure default values" })
			.click();
		await expect(page.getByRole("combobox", { name: /region/ })).toHaveText(
			"Europe",
		);
	} finally {
		if (id)
			expect((await api.delete(`/api/integrations/${id}`)).ok()).toBe(
				true,
			);
	}
});
