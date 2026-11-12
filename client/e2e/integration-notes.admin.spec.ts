import { test, expect } from "./fixtures/api-fixture";

test("integration setup instructions work across detail, defaults, and phone layouts", async ({
	page,
	api,
}) => {
	const name = `Integration notes ${Date.now()}`;
	let integrationId: string | undefined;

	try {
		const created = await api.post("/api/integrations", {
			data: {
				name,
				notes: "## Before You Begin\n\nCreate an API key and record the tenant ID.",
				config_schema: [
					{
						key: "api_base_url",
						type: "string",
						required: false,
						description: "Service endpoint",
					},
				],
			},
		});
		expect(created.ok(), await created.text()).toBe(true);
		integrationId = ((await created.json()) as { id: string }).id;

		await page.setViewportSize({ width: 1440, height: 900 });
		await page.goto(`/integrations/${integrationId}`);
		await expect(
			page.getByRole("region", { name: "Setup instructions" }),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Before You Begin" }),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/integration-notes-wide.png",
			fullPage: true,
			animations: "disabled",
		});

		await page
			.getByRole("button", { name: "Configure default values" })
			.click();
		const defaultsDialog = page.getByRole("dialog", {
			name: "Edit Configuration Defaults",
		});
		await expect(defaultsDialog).toBeVisible();
		await expect(
			defaultsDialog.getByRole("region", { name: "Setup instructions" }),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/integration-notes-defaults-dialog.png",
			fullPage: true,
			animations: "disabled",
		});
		await defaultsDialog.getByRole("button", { name: "Cancel" }).click();

		await page
			.getByRole("button", { name: "Edit setup instructions" })
			.click();
		const editorDialog = page.getByRole("dialog", {
			name: "Edit Setup Instructions",
		});
		const editor = editorDialog.getByRole("textbox", {
			name: "Setup instructions",
		});
		await expect(editor).toBeVisible();
		await editor.fill("Saved setup instructions");
		const saved = page.waitForResponse(
			(response) =>
				response.request().method() === "PUT" &&
				new URL(response.url()).pathname ===
					`/api/integrations/${integrationId}`,
		);
		await editorDialog
			.getByRole("button", { name: "Save Instructions" })
			.click();
		expect((await saved).ok()).toBe(true);
		await expect(editorDialog).toBeHidden();
		await page.reload();
		await expect(page.getByText("Saved setup instructions")).toBeVisible();

		const downloaded = page.waitForEvent("download");
		await page.getByRole("button", { name: "Export", exact: true }).click();
		const download = await downloaded;
		const stream = await download.createReadStream();
		const chunks: Buffer[] = [];
		for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
		const archive = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
			items: Array<{ name: string; notes?: string | null }>;
		};
		expect(archive.items).toHaveLength(1);
		expect(archive.items[0]).toMatchObject({
			name,
			notes: "## Saved setup instructions",
		});

		await page.setViewportSize({ width: 390, height: 844 });
		await expect(
			page.getByRole("region", { name: "Setup instructions" }),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/integration-notes-phone.png",
			fullPage: true,
			animations: "disabled",
		});
	} finally {
		if (integrationId) {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/integrations/${integrationId}`)).status(),
			);
		}
	}
});
