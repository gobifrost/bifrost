import { randomUUID } from "node:crypto";
import { expect, test } from "./fixtures/api-fixture";

test("configures a write-only webhook header token", async ({ page, api }) => {
	const name = `Header Token ${randomUUID()}`;
	let sourceId: string | undefined;
	try {
		await page.goto("/event-sources");
		await page.getByRole("button", { name: "Create event source" }).first().click();
		const dialog = page.getByRole("dialog", { name: "Create Event Source" });
		await dialog.getByLabel("Name").fill(name);
		await dialog.getByRole("combobox", { name: "Webhook Adapter" }).click();
		await page.getByRole("option", { name: "Generic Webhook" }).click();
		await dialog.getByRole("switch", { name: "Header Token Authentication" }).click();
		await dialog.getByLabel("Authentication Header").fill("X-Gitlab-Token");
		await dialog.getByLabel("Expected Header Value").fill("synthetic-token");
		await expect(dialog.getByLabel("Signature Header")).toHaveCount(0);
		const created = page.waitForResponse((response) => response.url().endsWith("/api/events/sources") && response.request().method() === "POST");
		await dialog.getByRole("button", { name: "Create Event Source" }).click();
		const response = await created;
		expect(response.status()).toBe(201);
		const source = await response.json();
		sourceId = source.id;
		expect(JSON.stringify(source)).not.toContain("synthetic-token");
		await expect(dialog).toBeHidden();
		await page.getByLabel("Search event sources...").fill(name);
		await page.getByRole("link", { name }).click();
		await page.getByRole("button", { name: / actions$/ }).click();
		await expect(page.getByRole("menuitem", { name: "Rotate signing secret" })).toHaveCount(0);
        await page.getByRole("menuitem", { name: "Set Authentication Token" }).click();
        const tokenDialog = page.getByRole("alertdialog", { name: "Set Authentication Token" });
        await tokenDialog.getByLabel("Expected Header Value").fill("synthetic-replacement");
        await tokenDialog.getByRole("button", { name: "Save Token" }).click();
        await expect(tokenDialog).toBeHidden();
        await page.getByRole("button", { name: / actions$/ }).click();
		await page.getByRole("menuitem", { name: "Edit" }).click();
		const edit = page.getByRole("dialog", { name: "Edit Event Source" });
		await expect(edit.getByLabel("Authentication Header")).toHaveValue("X-Gitlab-Token");
		await expect(edit.getByLabel("Expected Header Value")).toHaveValue("");
		await expect(edit.getByText("Secret configured")).toBeVisible();
	} finally {
		if (sourceId) await api.delete(`/api/events/sources/${sourceId}`);
	}
});
