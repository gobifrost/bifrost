import { test, expect } from "./fixtures/api-fixture";

test("integration setup instructions work across detail, defaults, and phone layouts", async ({
	page,
	api,
}) => {
	const name = `Integration notes ${Date.now()}`;
	let integrationId: string | undefined;
	const notes =
		"## Before You Begin\n\n" +
		Array.from(
			{ length: 60 },
			(_, i) => `Step ${i + 1}: Run the setup command.`,
		).join("\n\n") +
		"\n\n[Open form](/execute/example) with `inline code`.\n\n- Parent\n  - Child\n\nLast setup step";

	try {
		const created = await api.post("/api/integrations", {
			data: {
				name,
				notes,
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
		// A real scroll container must expose the end without scrolling away the toolbar.
		const scrollToEnd = async () => {
			const canScroll = await editor.evaluate((element) => {
				for (
					let node: HTMLElement | null = element as HTMLElement;
					node && node.getAttribute("role") !== "dialog";
					node = node.parentElement
				) {
					if (
						/(auto|scroll)/.test(
							getComputedStyle(node).overflowY,
						) &&
						node.scrollHeight > node.clientHeight
					) {
						node.scrollTop = node.scrollHeight;
						return node.scrollTop > 0;
					}
				}
				return false;
			});
			expect(canScroll).toBe(true);
			await expect(editor.getByText(/Last setup step/)).toBeInViewport();
			await expect(
				editorDialog.getByRole("group", {
					name: "Rich text formatting",
				}),
			).toBeInViewport();
		};
		await scrollToEnd();
		await editor.getByText("Last setup step", { exact: true }).click();
		await page.keyboard.press("End");
		await page.keyboard.type(" updated");
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
		await expect(
			page.getByText("Last setup step updated", { exact: true }),
		).toBeVisible();
		const readBack = await api.get(`/api/integrations/${integrationId}`);
		const stored = (await readBack.json()).notes as string;
		expect(stored).toContain("[Open form](/execute/example)");
		expect(stored).toContain("`inline code`");
		expect(stored).toMatch(/- Parent\n\s+- Child/);

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
			notes: stored,
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
		await page
			.getByRole("button", { name: "Edit setup instructions" })
			.click();
		await expect(editor).toBeVisible();
		await scrollToEnd();
		const unchangedSave = page.waitForResponse(
			(response) =>
				response.request().method() === "PUT" &&
				new URL(response.url()).pathname ===
					`/api/integrations/${integrationId}`,
		);
		await editorDialog
			.getByRole("button", { name: "Save Instructions" })
			.click();
		expect((await unchangedSave).ok()).toBe(true);
		await expect(editorDialog).toBeHidden();
		const unchanged = await api.get(`/api/integrations/${integrationId}`);
		expect((await unchanged.json()).notes).toBe(stored);
	} finally {
		if (integrationId) {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/integrations/${integrationId}`)
				).status(),
			);
		}
	}
});
