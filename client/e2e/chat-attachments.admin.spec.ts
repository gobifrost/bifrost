import { expect, test, type Locator, type Page } from "@playwright/test";

async function expectTouchTarget(locator: Locator) {
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

test.describe("Chat attachments and model profiles", () => {
	test("uploads a file with the selected profile and previews it", async ({
		page,
	}) => {
		await page.route("**/api/chat/model-profiles", async (route) => {
			const capabilities = {
				image_input: true,
				pdf_input: true,
				tool_calling: true,
				source: "verified",
				fingerprint: "e2e",
			};
			await route.fulfill({
				json: {
					profiles: [
						{
							id: "profile-fast",
							name: "Fast",
							label: "Fast",
							capabilities,
						},
						{
							id: "profile-balanced",
							name: "Balanced",
							label: "Balanced",
							capabilities,
						},
						{
							id: "profile-pro",
							name: "Pro",
							label: "Pro",
							capabilities,
						},
					],
					default_profile_id: "profile-pro",
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
		let showGenerating!: () => void;
		let showResponding!: () => void;
		let finishChat!: () => void;
		const chatPayload = new Promise<Record<string, unknown>>((resolve) => {
			resolveChat = resolve;
		});
		let runRequest: Record<string, unknown> | null = null;
		let sendSocketMessage:
			((payload: Record<string, unknown>) => void) | null = null;
		let streamStarted = false;
		const sendRunEvent = (
			sequence: number,
			kind: string,
			status: string,
			payload: Record<string, unknown>,
		) => {
			if (!sendSocketMessage || !runRequest) return;
			sendSocketMessage({
				type: "chat_run_event",
				protocol_version: 1,
				event_id: `00000000-0000-4000-8000-${String(sequence).padStart(12, "0")}`,
				sequence,
				conversation_id: String(runRequest.conversation_id),
				run_id: String(runRequest.client_run_id),
				occurred_at: new Date().toISOString(),
				kind,
				status,
				payload,
			});
		};
		const startStream = () => {
			if (streamStarted || !sendSocketMessage || !runRequest) return;
			streamStarted = true;
			const conversationId = String(runRequest.conversation_id);
			const userMessageId = String(runRequest.user_message_id);
			setTimeout(() => {
				sendRunEvent(1, "run_status", "running", {
					type: "run_status",
					conversation_id: conversationId,
					run_status: "running",
				});

				sendRunEvent(2, "message_start", "running", {
					type: "message_start",
					conversation_id: conversationId,
					user_message_id: userMessageId,
					local_id: userMessageId,
					assistant_message_id:
						"00000000-0000-4000-8000-000000000020",
				});

				sendRunEvent(3, "agent_switch", "running", {
					type: "agent_switch",
					conversation_id: conversationId,
					agent_switch: {
						agent_name: "Document Agent",
						agent_id: "agent-document",
						reason: "automatic",
					},
				});
			}, 100);
			showGenerating = () => {
				sendRunEvent(4, "delta", "running", {
					type: "delta",
					conversation_id: conversationId,
					content: "I’ll create that. ",
				});

				sendRunEvent(5, "assistant_message_end", "running", {
					type: "assistant_message_end",
					conversation_id: conversationId,
					message_id: "00000000-0000-4000-8000-000000000021",
				});

				sendRunEvent(6, "tool_call", "running", {
					type: "tool_call",
					conversation_id: conversationId,
					message_id: "00000000-0000-4000-8000-000000000022",
					tool_call: {
						id: "artifact-tool-call",
						name: "create_text_artifact",
						arguments: {
							filename: "Generated Report.md",
							format: "markdown",
						},
					},
				});
			};
			showResponding = () => {
				sendRunEvent(7, "tool_result", "running", {
					type: "tool_result",
					conversation_id: conversationId,
					message_id: "00000000-0000-4000-8000-000000000022",
					tool_result: {
						tool_call_id: "artifact-tool-call",
						tool_name: "create_text_artifact",
						result: { type: "bifrost_artifact" },
						duration_ms: 304,
					},
				});
				sendRunEvent(8, "artifact_ready", "running", {
					type: "artifact_ready",
					conversation_id: conversationId,
					message_id: "00000000-0000-4000-8000-000000000022",
					artifact: {
						type: "bifrost_artifact",
						id: "generated-artifact",
						filename: "Generated Report.md",
						content_type: "text/markdown",
						size_bytes: 40,
					},
				});

				sendRunEvent(9, "delta", "running", {
					type: "delta",
					conversation_id: conversationId,
					content: "I created the report.",
				});
			};
			finishChat = () => {
				sendRunEvent(10, "done", "completed", {
					type: "done",
					conversation_id: conversationId,
					message_id: "00000000-0000-4000-8000-000000000020",
					content: "I created the report.",
					duration_ms: 1_240,
					run_status: "completed",
				});
			};
		};
		await page.route("**/api/chat/runs", async (route) => {
			runRequest = route.request().postDataJSON() as Record<
				string,
				unknown
			>;
			resolveChat(runRequest);
			const conversationId = String(runRequest.conversation_id);
			const userMessageId = String(runRequest.user_message_id);
			await route.fulfill({
				status: 201,
				json: {
					run_id: runRequest.client_run_id,
					conversation: {
						id: conversationId,
						agent_id: null,
						user_id: "00000000-0000-4000-8000-000000000001",
						channel: "chat",
						title: null,
						is_active: true,
						created_at: new Date().toISOString(),
						updated_at: new Date().toISOString(),
						message_count: 1,
						agent_name: null,
					},
					user_message: {
						id: userMessageId,
						conversation_id: conversationId,
						role: "user",
						content: runRequest.content,
						attachments: [
							{
								id: (runRequest.attachment_ids as string[])[0],
								conversation_id: conversationId,
								filename: "notes.txt",
								content_type: "text/plain",
								size_bytes: 18,
							},
						],
						sequence: 1,
						created_at: new Date().toISOString(),
					},
					status: "queued",
					idempotent: false,
				},
			});
			startStream();
		});
		await page.route("**/api/chat/conversations/*/state", async (route) => {
			const segments = new URL(route.request().url()).pathname.split("/");
			const conversationId = segments.at(-2)!;
			await route.fulfill({
				json: {
					conversation: {
						id: conversationId,
						agent_id: null,
						user_id: "00000000-0000-4000-8000-000000000001",
						channel: "chat",
						title: null,
						is_active: true,
						created_at: new Date().toISOString(),
						updated_at: new Date().toISOString(),
						message_count: runRequest ? 1 : 0,
						agent_name: null,
					},
					active_run: runRequest
						? {
								id: runRequest.client_run_id,
								conversation_id: conversationId,
								agent_id: null,
								status: "queued",
								error: null,
								created_at: new Date().toISOString(),
								started_at: null,
								completed_at: null,
							}
						: null,
					messages: runRequest
						? [
								{
									id: runRequest.user_message_id,
									conversation_id: conversationId,
									role: "user",
									content: runRequest.content,
									attachments: [
										{
											id: (
												runRequest.attachment_ids as string[]
											)[0],
											conversation_id: conversationId,
											filename: "notes.txt",
											content_type: "text/plain",
											size_bytes: 18,
										},
									],
									sequence: 1,
									created_at: new Date().toISOString(),
								},
							]
						: [],
					events: [],
					latest_sequence: 0,
				},
			});
		});
		await page.routeWebSocket(/\/ws\/connect/, (socket) => {
			sendSocketMessage = (payload) =>
				socket.send(JSON.stringify(payload));
			startStream();
			socket.onMessage((raw) => {
				const payload = JSON.parse(String(raw)) as Record<
					string,
					unknown
				>;
				if (payload.type === "subscribe") {
					for (const channel of (payload.channels as string[]) ??
						[]) {
						sendSocketMessage?.({ type: "subscribed", channel });
					}
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
		const userMessage = page.getByText("Summarize this file", {
			exact: true,
		});
		await expect(page.getByLabel("Chat input")).toHaveValue("");
		await expect(userMessage).toBeVisible();
		const thinking = page.getByText("Thinking…");
		await expect(thinking).toBeVisible();
		await page.screenshot({
			path: "playwright-results/screenshots/chat-thinking.png",
			fullPage: true,
		});
		showGenerating();
		await expect(page.getByText("Generating Markdown…")).toBeVisible();
		await expect(userMessage).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Stop generation" }),
		).toBeVisible();
		await expect(page.getByText(/Worked for/i)).toHaveCount(0);
		await page.screenshot({
			path: "playwright-results/screenshots/chat-generating.png",
			fullPage: true,
		});
		showResponding();
		await expect(page.getByText("Responding…")).toBeVisible();
		await expect(userMessage).toBeVisible();
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
		expect(payload.content).toBe("Summarize this file");
		expect(payload.client_run_id).toEqual(expect.any(String));
		expect(payload.user_message_id).toEqual(expect.any(String));
		expect(payload.model_profile_id).toBe("profile-pro");
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

		await expect(page.getByText(/Worked for/i)).toBeVisible();
		await expectTouchTarget(
			page.getByRole("button", { name: /Worked for/i }),
		);
		await expectNoHorizontalOverflow(page);
		await expect(
			page.locator('[aria-busy="true"]', {
				hasText: "I created the report.",
			}),
		).toHaveCount(0);
		await page.getByRole("button", { name: /Worked for/i }).click();
		await expect(
			page.getByText("Routed to", { exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("Document Agent", { exact: true }),
		).toBeVisible();
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
		await expect(
			page.getByRole("button", { name: "Preview Generated Report.md" }),
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
