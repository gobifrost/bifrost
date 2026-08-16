import { expect, test } from "@playwright/test";

test.describe("Chat attachments and model tiers", () => {
	test("uploads a file with the selected tier and previews it", async ({
		page,
	}) => {
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
			const capabilities = {
				image_input: true,
				pdf_input: true,
				tool_calling: true,
				native_image_output: false,
				source: "verified",
				fingerprint: "e2e",
			};
			await route.fulfill({
				json: {
					tiers: [
						{ id: "fast", label: "Fast", capabilities },
						{ id: "balanced", label: "Balanced", capabilities },
						{ id: "pro", label: "Pro", capabilities },
					],
					default_tier: "balanced",
				},
			});
		});
		await page.route(
			"**/attachments/generated-artifact/content*",
			async (route) => {
				await route.fulfill({
					contentType: "text/markdown",
					body: "# Generated report\n\nReady to download.",
				});
			},
		);

		let resolveChat!: (payload: Record<string, unknown>) => void;
		const chatPayload = new Promise<Record<string, unknown>>((resolve) => {
			resolveChat = resolve;
		});
		await page.routeWebSocket(/\/ws\/connect/, (socket) => {
			socket.onMessage((raw) => {
				const payload = JSON.parse(String(raw)) as Record<
					string,
					unknown
				>;
				if (payload.type === "chat") {
					resolveChat(payload);
					const conversationId = String(payload.conversation_id);
					socket.send(JSON.stringify({
						type: "message_start",
						conversation_id: conversationId,
						assistant_message_id: "assistant-message",
					}));
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "tool_call",
								conversation_id: conversationId,
								message_id: "artifact-tool-message",
								tool_call: {
									id: "artifact-tool-call",
									name: "create_text_artifact",
									arguments: {
										filename: "Generated Report.md",
										format: "markdown",
									},
								},
							}),
						);
					}, 250);
					setTimeout(() => {
						socket.send(JSON.stringify({
							type: "tool_result",
							conversation_id: conversationId,
							message_id: "artifact-tool-message",
							tool_result: {
								tool_call_id: "artifact-tool-call",
								tool_name: "create_text_artifact",
								result: { type: "bifrost_artifact" },
								duration_ms: 304,
							},
						}));
						socket.send(
							JSON.stringify({
								type: "artifact_ready",
								conversation_id: conversationId,
								message_id: "artifact-tool-message",
								artifact: {
									type: "bifrost_artifact",
									filename: "Generated Report.md",
									content_type: "text/markdown",
									size_bytes: 40,
									attachment_id: "generated-artifact",
									conversation_id: conversationId,
									created_at: "2026-08-15T00:00:00Z",
								},
							}),
						);
						socket.send(JSON.stringify({
							type: "delta",
							conversation_id: conversationId,
							content: "I created the report.",
						}));
						socket.send(JSON.stringify({
							type: "done",
							conversation_id: conversationId,
							duration_ms: 1_240,
						}));
					}, 600);
				}
				if (payload.type === "ping")
					socket.send(JSON.stringify({ type: "pong" }));
			});
		});

		await page.goto("/chat");
		await page.getByRole("combobox", { name: "Response model" }).click();
		await page.getByRole("option", { name: "Pro" }).click();

		const chooser = page.waitForEvent("filechooser");
		await page.getByRole("button", { name: "Attach files" }).click();
		await (
			await chooser
		).setFiles({
			name: "notes.txt",
			mimeType: "text/plain",
			buffer: Buffer.from("attachment preview"),
		});
		await expect(page.getByText("notes.txt")).toBeVisible();

		await page.getByLabel("Chat input").fill("Summarize this file");
		await page.getByRole("button", { name: "Send message" }).click();
		await expect(page.getByText("Thinking…")).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-thinking.png",
			fullPage: true,
		});
		await expect(page.getByText("Generating Markdown…")).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-generating.png",
			fullPage: true,
		});

		const payload = await chatPayload;
		expect(payload.message).toBe("Summarize this file");
		expect(payload.model_tier).toBe("pro");
		expect(payload.attachment_ids).toEqual([expect.any(String)]);

		await page.getByRole("button", { name: /^notes\.txt \d+ B$/ }).click();
		await expect(page.getByRole("dialog")).toContainText(
			"attachment preview",
		);
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();
		await page.getByRole("button", { name: /close/i }).click();
		await expect(page.getByRole("dialog")).not.toBeVisible();

		await expect(page.getByText("Worked for 1s")).toBeVisible();
		await page.getByRole("button", { name: /Worked for 1s/i }).click();
		await page
			.getByRole("button", { name: /create_text_artifact/i })
			.click();
		await expect(page.getByRole("heading", { name: "Result" })).toBeVisible();
		await expect(page.getByRole("dialog")).not.toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-complete.png",
			fullPage: true,
		});
		await page.getByRole("button", { name: /Generated Report\.md/i }).click();
		await expect(page.getByRole("dialog")).toContainText(
			"Generated report",
		);
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();

		await page.setViewportSize({ width: 390, height: 844 });
		await expect(page.getByRole("dialog")).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();
	});

	test("browses the persistent artifact library", async ({ page }) => {
		await page.route("**/api/chat/artifacts", async (route) => {
			await route.fulfill({
				json: [
					{
						id: "generated-artifact",
						conversation_id: "conversation-1",
						message_id: "message-1",
						filename: "Bifrost Welcome Page.html",
						content_type: "text/html",
						size_bytes: 16384,
						kind: "artifact",
						conversation_title: "Bifrost welcome",
						created_at: "2026-08-15T18:00:00Z",
					},
					{
						id: "uploaded-source",
						conversation_id: "conversation-2",
						message_id: "message-2",
						filename: "Brand Notes.md",
						content_type: "text/markdown",
						size_bytes: 2480,
						kind: "attachment",
						conversation_title: "Launch brief",
						created_at: "2026-08-14T16:00:00Z",
					},
				],
			});
		});

		await page.goto("/chat/artifacts");
		await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();
		await expect(page.getByText("Bifrost Welcome Page.html")).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-library.png",
			fullPage: true,
		});

		await page.setViewportSize({ width: 390, height: 844 });
		await page.reload();
		await expect(page.getByRole("heading", { name: "Artifacts" })).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-library-mobile.png",
			fullPage: true,
		});
	});
});
