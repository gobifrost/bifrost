import { expect, test, type Page } from "./fixtures/api-fixture";

type EmbeddingConfig = {
	connection_id: string | null;
	model: string;
	is_configured: boolean;
};

async function authenticatedJson(
	page: Page,
	path: string,
	options: {
		method?: string;
		body?: Record<string, unknown>;
		mcp?: boolean;
	} = {},
) {
	return page.evaluate(
		async ({ path, method, body, mcp }) => {
			const token = localStorage.getItem("bifrost_access_token");
			const csrf = document.cookie.match(
				/(?:^|;\s*)csrf_token=([^;]+)/,
			)?.[1];
			const headers: Record<string, string> = {
				"Content-Type": "application/json",
			};
			if (token) headers.Authorization = `Bearer ${token}`;
			if (csrf) headers["X-CSRF-Token"] = csrf;
			if (mcp) headers.Accept = "application/json, text/event-stream";

			const response = await fetch(path, {
				method: method ?? "GET",
				headers,
				credentials: "include",
				body: body ? JSON.stringify(body) : undefined,
			});
			const text = await response.text();
			return {
				status: response.status,
				body: text ? (JSON.parse(text) as unknown) : null,
			};
		},
		{
			path,
			method: options.method,
			body: options.body,
			mcp: options.mcp,
		},
	);
}

async function expectOk(
	result: Awaited<ReturnType<typeof authenticatedJson>>,
	message: string,
) {
	expect(
		result.status,
		`${message}: ${JSON.stringify(result.body)}`,
	).toBeLessThan(300);
}

test.describe("Private memory", () => {
	test("enables and manages private memory", async ({ page }, testInfo) => {
		await page.goto("/settings/ai-memory");
		const currentUser = await authenticatedJson(page, "/api/auth/me");
		const organizationId = (currentUser.body as { organization_id: string })
			.organization_id;
		const memoryTitle = `Acme onboarding ${Date.now()}`;
		const memoryChecklist = `Northwind tenant checklist ${Date.now()}`;
		const platformMemorySettings = await authenticatedJson(
			page,
			"/api/admin/memory/settings",
		);
		expect(platformMemorySettings.status).toBe(200);
		const userMemorySettings = await authenticatedJson(
			page,
			"/api/memory/settings",
		);
		expect(userMemorySettings.status).toBe(200);
		const embeddingConfig = await authenticatedJson(
			page,
			"/api/admin/llm/embedding-config",
		);
		expect(embeddingConfig.status).toBe(200);
		const globalInstructions = await authenticatedJson(
			page,
			"/api/admin/required-instructions",
		);
		expect(globalInstructions.status).toBe(200);
		const organizationInstructions = await authenticatedJson(
			page,
			`/api/admin/required-instructions/organizations/${organizationId}`,
		);
		expect(organizationInstructions.status).toBe(200);

		let fixtureConnectionId: string | null = null;
		let memoryId: string | null = null;
		try {
			const fixtureConnection = await authenticatedJson(
				page,
				"/api/admin/ai/connections",
				{
					method: "POST",
					body: {
						name: `Memory E2E Fixture Embeddings ${Date.now()}`,
						provider: "openai_compatible",
						api_key: "fixture-key",
						endpoint: "http://scheduler-fixtures:8080/v1",
					},
				},
			);
			expect(fixtureConnection.status).toBe(201);
			fixtureConnectionId = (fixtureConnection.body as { id: string }).id;

			await expectOk(
				await authenticatedJson(page, "/api/admin/memory/settings", {
					method: "PUT",
					body: { enabled: false },
				}),
				"disable platform memory before test",
			);
			const clearEmbedding = await authenticatedJson(
				page,
				"/api/admin/llm/embedding-config",
				{
					method: "DELETE",
				},
			);
			expect([204, 404]).toContain(clearEmbedding.status);
			await expectOk(
				await authenticatedJson(
					page,
					"/api/admin/required-instructions",
					{
						method: "PUT",
						body: { instructions: "" },
					},
				),
				"clear global instructions before test",
			);
			await expectOk(
				await authenticatedJson(
					page,
					`/api/admin/required-instructions/organizations/${organizationId}`,
					{ method: "PUT", body: { instructions: "" } },
				),
				"clear organization instructions before test",
			);
			const embedding = await authenticatedJson(
				page,
				"/api/admin/llm/embedding-config",
				{
					method: "POST",
					body: {
						connection_id: fixtureConnectionId,
						model: "fixture-embedding",
						confirm_reindex: true,
					},
				},
			);
			expect(embedding.status).toBe(200);
			expect((embedding.body as { saved: boolean }).saved).toBe(true);

			const platformToggle = page.getByRole("switch", {
				name: "Enable Memory",
			});
			await expect(platformToggle).toBeEnabled();
			await expect(platformToggle).not.toBeChecked();
			await platformToggle.click();
			await expect(platformToggle).toBeChecked();
			const platformToastClose = page
				.getByRole("button", { name: "Close toast" })
				.last();
			await platformToastClose.click();
			await expect(platformToastClose).toBeHidden();
			await page
				.getByText("Users can disable memory in their preferences.")
				.scrollIntoViewIfNeeded();
			await testInfo.attach("AI settings — Memory", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			await page.goto("/settings/ai-instructions");
			const globalEditor = page.locator(
				'[aria-label="Global Instructions editor"]',
			);
			await globalEditor.scrollIntoViewIfNeeded();
			await globalEditor.fill(
				"Confirm the customer and summarize any destructive action before execution.",
			);
			await page
				.getByRole("button", { name: "Save Instructions" })
				.click();
			await expect(
				page.getByText("Global Instructions saved"),
			).toBeVisible();
			await testInfo.attach("AI settings — Global instructions", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			await page.goto("/organizations");
			await page
				.getByRole("row")
				.filter({ hasText: organizationId })
				.click();
			await page.getByRole("tab", { name: "Instructions" }).click();
			const organizationEditor = page.locator(
				'[aria-label="Organization Instructions editor"]',
			);
			await organizationEditor.fill(
				"Use the organization onboarding runbook before provisioning access.",
			);
			await page
				.getByRole("button", { name: "Save Instructions" })
				.click();
			await expect(
				page.getByText("Organization Instructions saved"),
			).toBeVisible();
			await testInfo.attach("Organization instructions", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			const requiredInstructions = await authenticatedJson(page, "/mcp", {
				method: "POST",
				mcp: true,
				body: {
					jsonrpc: "2.0",
					id: 2,
					method: "tools/call",
					params: {
						name: "bifrost_get_required_instructions",
						arguments: {},
					},
				},
			});
			expect(requiredInstructions.status).toBe(200);
			const resolved = (
				requiredInstructions.body as {
					result: { structuredContent: { instructions: string[] } };
				}
			).result.structuredContent.instructions;
			expect(resolved[0]).toContain("# Memory");
			expect(resolved).toContain(
				"# Global Instructions\n\nConfirm the customer and summarize any destructive action before execution.",
			);
			expect(resolved).toContain(
				"# Organization Instructions\n\nUse the organization onboarding runbook before provisioning access.",
			);

			const userDefault = await authenticatedJson(
				page,
				"/api/memory/settings",
			);
			if (
				(userDefault.body as { user_enabled: boolean }).user_enabled ===
				false
			) {
				await authenticatedJson(page, "/api/memory/settings", {
					method: "PUT",
					body: { enabled: true },
				});
			}
			await page.goto("/user-settings/preferences");

			const userToggle = page.getByRole("switch", {
				name: "Enable Memory",
			});
			await expect(userToggle).toBeEnabled();
			await expect(userToggle).toBeChecked();
			await expect(
				page.getByText(
					"Only your account can search or manage these memories.",
				),
			).toBeVisible();
			await testInfo.attach("Preferences — Memory enabled by default", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			await userToggle.click();
			await expect(userToggle).not.toBeChecked();
			const userToastClose = page
				.getByRole("button", { name: "Close toast" })
				.last();
			await userToastClose.click();
			await expect(userToastClose).toBeHidden();
			await userToggle.click();
			await expect(userToggle).toBeChecked();
			const reenabledToastClose = page
				.getByRole("button", { name: "Close toast" })
				.last();
			await reenabledToastClose.click();
			await expect(reenabledToastClose).toBeHidden();
			await expect(
				page.getByText("Saved Memories", { exact: true }),
			).toBeVisible();
			const saved = await authenticatedJson(page, "/mcp", {
				method: "POST",
				mcp: true,
				body: {
					jsonrpc: "2.0",
					id: 1,
					method: "tools/call",
					params: {
						name: "bifrost_save_memory",
						arguments: {
							content: `# ${memoryTitle}\n\nUse the **${memoryChecklist}** before provisioning access.`,
							metadata: { customer: "acme" },
						},
					},
				},
			});
			expect(saved.status).toBe(200);
			memoryId = (
				saved.body as {
					result: { structuredContent: { id: string } };
				}
			).result.structuredContent.id;

			await page.reload();
			await expect(page.getByText(memoryTitle)).toBeVisible();
			await expect(page.getByText(memoryChecklist)).toBeVisible();
			await page
				.getByText("Saved Memories", { exact: true })
				.scrollIntoViewIfNeeded();
			await testInfo.attach("Memory management — Saved memory", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			const savedMemory = page
				.getByRole("textbox", { name: "Saved memory" })
				.filter({ hasText: memoryTitle });
			await savedMemory
				.locator(
					"xpath=ancestor::div[contains(@class, 'space-y-3')][1]",
				)
				.getByRole("button", { name: "Remove memory" })
				.click();
			await page.getByRole("button", { name: /^Remove$/ }).click();
			await expect(page.getByText(memoryTitle)).toBeHidden();
			memoryId = null;
		} finally {
			if (memoryId) {
				const deleteMemory = await authenticatedJson(
					page,
					`/api/memory/${memoryId}`,
					{ method: "DELETE" },
				);
				expect([200, 404]).toContain(deleteMemory.status);
			}
			await expectOk(
				await authenticatedJson(page, "/api/memory/settings", {
					method: "PUT",
					body: {
						enabled: (
							userMemorySettings.body as { user_enabled: boolean }
						).user_enabled,
					},
				}),
				"restore user memory setting",
			);
			await expectOk(
				await authenticatedJson(page, "/api/admin/memory/settings", {
					method: "PUT",
					body: {
						enabled: (
							platformMemorySettings.body as { enabled: boolean }
						).enabled,
					},
				}),
				"restore platform memory setting",
			);
			await expectOk(
				await authenticatedJson(
					page,
					"/api/admin/required-instructions",
					{
						method: "PUT",
						body: {
							instructions: (
								globalInstructions.body as {
									instructions: string;
								}
							).instructions,
						},
					},
				),
				"restore global instructions",
			);
			await expectOk(
				await authenticatedJson(
					page,
					`/api/admin/required-instructions/organizations/${organizationId}`,
					{
						method: "PUT",
						body: {
							instructions: (
								organizationInstructions.body as {
									instructions: string;
								}
							).instructions,
						},
					},
				),
				"restore organization instructions",
			);
			const previousEmbedding = embeddingConfig.body as EmbeddingConfig;
			if (
				previousEmbedding.is_configured &&
				previousEmbedding.connection_id
			) {
				await expectOk(
					await authenticatedJson(
						page,
						"/api/admin/llm/embedding-config",
						{
							method: "POST",
							body: {
								connection_id: previousEmbedding.connection_id,
								model: previousEmbedding.model,
								confirm_reindex: true,
							},
						},
					),
					"restore embedding config",
				);
			} else {
				const clearRestoredEmbedding = await authenticatedJson(
					page,
					"/api/admin/llm/embedding-config",
					{ method: "DELETE" },
				);
				expect([204, 404]).toContain(clearRestoredEmbedding.status);
			}
			if (fixtureConnectionId) {
				await expectOk(
					await authenticatedJson(
						page,
						`/api/admin/ai/connections/${fixtureConnectionId}`,
						{ method: "DELETE" },
					),
					"delete fixture embedding connection",
				);
			}
		}
	});
});
