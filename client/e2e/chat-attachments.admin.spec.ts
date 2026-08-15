import { expect, test } from "@playwright/test";

test.describe("Chat attachments and model tiers", () => {
	test("uploads a file with the selected tier and previews it", async ({ page }) => {
		await page.route("**/api/admin/llm/config", async (route) => {
			await route.fulfill({
				json: {
					provider: "openai",
					model: "balanced-model",
					max_tokens: 16384,
					is_configured: true,
					api_key_set: true,
				},
			});
		});
		await page.route("**/api/chat/model-tiers", async (route) => {
			await route.fulfill({
				json: {
					tiers: [
						{ id: "fast", label: "Fast" },
						{ id: "balanced", label: "Balanced" },
						{ id: "pro", label: "Pro" },
					],
					default_tier: "balanced",
				},
			});
		});

		let resolveChat!: (payload: Record<string, unknown>) => void;
		const chatPayload = new Promise<Record<string, unknown>>((resolve) => {
			resolveChat = resolve;
		});
		await page.routeWebSocket(/\/ws\/connect/, (socket) => {
			socket.onMessage((raw) => {
				const payload = JSON.parse(String(raw)) as Record<string, unknown>;
				if (payload.type === "chat") resolveChat(payload);
				if (payload.type === "ping") socket.send(JSON.stringify({ type: "pong" }));
			});
		});

		await page.goto("/chat");
		await page.getByRole("combobox", { name: "Response model" }).click();
		await page.getByRole("option", { name: "Pro" }).click();

		const chooser = page.waitForEvent("filechooser");
		await page.getByRole("button", { name: "Attach files" }).click();
		await (await chooser).setFiles({
			name: "notes.txt",
			mimeType: "text/plain",
			buffer: Buffer.from("attachment preview"),
		});
		await expect(page.getByText("notes.txt")).toBeVisible();

		await page.getByLabel("Chat input").fill("Summarize this file");
		await page.getByRole("button", { name: "Send message" }).click();

		const payload = await chatPayload;
		expect(payload.message).toBe("Summarize this file");
		expect(payload.model_tier).toBe("pro");
		expect(payload.attachment_ids).toEqual([expect.any(String)]);

		await page
			.getByRole("button", { name: /^notes\.txt \d+ B$/ })
			.click();
		await expect(page.getByRole("dialog")).toContainText("attachment preview");
		await expect(page.getByRole("link", { name: "Download" })).toHaveAttribute(
			"href",
			/\/content\?download=true$/,
		);

		await page.setViewportSize({ width: 390, height: 844 });
		await expect(page.getByRole("dialog")).toBeVisible();
		await expect(page.getByRole("link", { name: "Download" })).toBeVisible();
	});
});
