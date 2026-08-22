import { expect, test, type Locator, type Page } from "@playwright/test";

async function expectTouchTarget(locator: Locator) {
	await locator.evaluate(async (element) => {
		const containingAnimations = document
			.getAnimations()
			.filter((animation) => {
				const target = (animation.effect as KeyframeEffect | null)
					?.target;
				return target instanceof Element && target.contains(element);
			});
		await Promise.allSettled(
			containingAnimations.map((animation) => animation.finished),
		);
	});
	const box = await locator.boundingBox();
	expect(box).not.toBeNull();
	expect(box!.width).toBeGreaterThanOrEqual(44);
	expect(box!.height).toBeGreaterThanOrEqual(44);
}

async function expectNoHorizontalOverflow(page: Page) {
	const dimensions = await page.evaluate(() => ({
		clientWidth: document.documentElement.clientWidth,
		scrollWidth: document.documentElement.scrollWidth,
	}));
	expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
}

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
		let finishChat!: () => void;
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
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "message_start",
								conversation_id: conversationId,
								assistant_message_id: "assistant-message",
							}),
						);
					}, 500);
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "agent_switch",
								conversation_id: conversationId,
								agent_switch: {
									agent_name: "Document Agent",
									agent_id: "agent-document",
									reason: "automatic",
								},
							}),
						);
					}, 600);
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "delta",
								conversation_id: conversationId,
								content: "I’ll create that. ",
							}),
						);
					}, 750);
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "assistant_message_end",
								conversation_id: conversationId,
								message_id: "assistant-progress",
							}),
						);
					}, 900);
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
					}, 1_000);
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "tool_result",
								conversation_id: conversationId,
								message_id: "artifact-tool-message",
								tool_result: {
									tool_call_id: "artifact-tool-call",
									tool_name: "create_text_artifact",
									result: { type: "bifrost_artifact" },
									duration_ms: 304,
								},
							}),
						);
						socket.send(
							JSON.stringify({
								type: "artifact_ready",
								conversation_id: conversationId,
								message_id: "artifact-tool-message",
								artifact: {
									type: "bifrost_artifact",
									id: "generated-artifact",
									filename: "Generated Report.md",
									content_type: "text/markdown",
									size_bytes: 40,
								},
							}),
						);
					}, 1_500);
					setTimeout(() => {
						socket.send(
							JSON.stringify({
								type: "delta",
								conversation_id: conversationId,
								content: "I created the report.",
							}),
						);
					}, 1_800);
					finishChat = () => {
						socket.send(
							JSON.stringify({
								type: "done",
								conversation_id: conversationId,
								message_id: "assistant-message",
								duration_ms: 1_240,
							}),
						);
					};
				}
				if (payload.type === "ping")
					socket.send(JSON.stringify({ type: "pong" }));
			});
		});

		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto("/chat");
		await expectTouchTarget(
			page.getByRole("button", { name: "Attach files" }),
		);
		await expectTouchTarget(
			page.getByRole("button", { name: "Send message" }),
		);
		await expectNoHorizontalOverflow(page);
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
		await expect(
			page.getByRole("button", { name: /Generating Markdown/i }),
		).toHaveAttribute("aria-expanded", "false");
		await expect(
			page.getByRole("button", { name: "Stop generation" }),
		).toBeVisible();
		await expect(page.getByText(/Worked for/i)).toHaveCount(0);
		await page.screenshot({
			path: "playwright-results/screenshots/chat-generating.png",
			fullPage: true,
		});
		await expect(page.getByText("Responding…")).toBeVisible();
		await expect(
			page.getByRole("button", { name: /Responding/i }),
		).toHaveAttribute("aria-expanded", "false");
		await expect(
			page.locator('[aria-busy="true"]', {
				hasText: "I created the report.",
			}),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-responding.png",
			fullPage: true,
		});

		const payload = await chatPayload;
		expect(payload.message).toBe("Summarize this file");
		expect(payload.model_tier).toBe("pro");
		expect(payload.attachment_ids).toEqual([expect.any(String)]);
		finishChat();

		await expect(
			page.getByRole("button", { name: "Download notes.txt" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Preview notes.txt" }).click();
		await expect(page.getByRole("dialog")).toContainText(
			"attachment preview",
		);
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();
		await page.getByRole("button", { name: /close/i }).click();
		await expect(page.getByRole("dialog")).not.toBeVisible();

		await expect(page.getByText("Worked for 1s")).toBeVisible();
		await expectTouchTarget(
			page.getByRole("button", { name: /Worked for 1s/i }),
		);
		await expectNoHorizontalOverflow(page);
		await expect(
			page.locator('[aria-busy="true"]', {
				hasText: "I created the report.",
			}),
		).toHaveCount(0);
		await page.getByRole("button", { name: /Worked for 1s/i }).click();
		await page
			.getByRole("button", { name: /create_text_artifact/i })
			.click();
		await expect(
			page.getByRole("heading", { name: "Result" }),
		).toBeVisible();
		await expect(page.getByRole("dialog")).not.toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-complete.png",
			fullPage: true,
		});
		await expect(
			page.getByRole("button", { name: "Copy message" }).first(),
		).toBeAttached();
		await page
			.getByRole("button", { name: "Preview Generated Report.md" })
			.click();
		await expect(page.getByRole("dialog")).toContainText(
			"Generated report",
		);
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();

		await expect(page.getByRole("dialog")).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();
	});

	test("browses the persistent artifact library", async ({ page }) => {
		await page.route(
			"**/attachments/generated-video/content*",
			async (route) => {
				await route.fulfill({
					contentType: "video/mp4",
					body: Buffer.from("00000018667479706d703432", "hex"),
				});
			},
		);
		await page.route(
			"**/attachments/generated-image/content*",
			async (route) => {
				await route.fulfill({
					contentType: "image/png",
					body: Buffer.from(
						"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
						"base64",
					),
				});
			},
		);
		await page.route("**/api/chat/artifacts", async (route) => {
			await route.fulfill({
				json: [
					{
						id: "generated-video",
						conversation_id: "conversation-1",
						message_id: "message-video",
						filename: "Launch Loop.mp4",
						content_type: "video/mp4",
						size_bytes: 2400000,
						kind: "artifact",
						conversation_title: "Bifrost welcome",
						created_at: "2026-08-16T18:00:00Z",
					},
					{
						id: "generated-image",
						conversation_id: "conversation-1",
						message_id: "message-image",
						filename: "Launch Concept.png",
						content_type: "image/png",
						size_bytes: 480000,
						kind: "artifact",
						conversation_title: "Bifrost welcome",
						created_at: "2026-08-16T17:00:00Z",
					},
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
		await expect(
			page.getByRole("heading", { name: "Artifacts" }),
		).toBeVisible();
		await expect(page.getByText("Bifrost Welcome Page.html")).toBeVisible();
		await page
			.getByRole("button", { name: "Preview Launch Loop.mp4" })
			.click();
		await expect(page.locator("video")).toBeVisible();
		await expect(page.getByText("1 / 2")).toBeVisible();
		await page.getByRole("button", { name: "Next media" }).click();
		await expect(
			page.getByRole("heading", { name: "Launch Concept.png" }),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-preview.png",
			fullPage: true,
		});
		await page.getByRole("button", { name: /close/i }).click();
		await expect(page.getByRole("dialog")).not.toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-library.png",
			fullPage: true,
		});

		await page.setViewportSize({ width: 390, height: 844 });
		await page.reload();
		await expect(
			page.getByRole("heading", { name: "Artifacts" }),
		).toBeVisible();
		await expectTouchTarget(
			page.getByRole("button", { name: "Manage Launch Loop.mp4" }),
		);
		await expectNoHorizontalOverflow(page);
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-library-mobile.png",
			fullPage: true,
		});
		await page
			.getByRole("button", { name: "Preview Launch Concept.png" })
			.click();
		await expect(page.getByRole("dialog")).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Launch Concept.png" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Previous media" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Next media" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Download" }),
		).toBeVisible();
		await expectTouchTarget(
			page.getByRole("button", { name: "Previous media" }),
		);
		await expectTouchTarget(
			page.getByRole("button", { name: "Next media" }),
		);
		await expectTouchTarget(page.getByRole("button", { name: "Download" }));
		await expectNoHorizontalOverflow(page);
		await expect(
			page.locator('img[alt="Launch Concept.png"]'),
		).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/artifact-preview-mobile.png",
			fullPage: true,
		});

		await page.setViewportSize({ width: 320, height: 568 });
		await expect(page.getByRole("dialog")).toBeVisible();
		await expectNoHorizontalOverflow(page);
	});
});
