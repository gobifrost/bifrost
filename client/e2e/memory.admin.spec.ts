import { expect, test, type Page } from "@playwright/test";

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

test.describe("Private memory", () => {
	test("enables and manages private memory", async ({ page }, testInfo) => {
		await page.goto("/settings/ai");
		const currentUser = await authenticatedJson(page, "/api/auth/me");
		const organizationId = (
			currentUser.body as { organization_id: string }
		).organization_id;
		const existingMemories = await authenticatedJson(page, "/api/memory");
		if (existingMemories.status === 200) {
			for (const memory of (
				existingMemories.body as { entries: Array<{ id: string }> }
			).entries) {
				await authenticatedJson(page, `/api/memory/${memory.id}`, {
					method: "DELETE",
				});
			}
		}
		await authenticatedJson(page, "/api/admin/memory/settings", {
			method: "PUT",
			body: { enabled: false },
		});
		await authenticatedJson(page, "/api/admin/llm/embedding-config", {
			method: "DELETE",
		});
		await authenticatedJson(page, "/api/admin/required-instructions", {
			method: "PUT",
			body: { instructions: "" },
		});
		await authenticatedJson(
			page,
			`/api/admin/required-instructions/organizations/${organizationId}`,
			{ method: "PUT", body: { instructions: "" } },
		);
		const embedding = await authenticatedJson(
			page,
			"/api/admin/llm/embedding-config",
			{
				method: "POST",
				body: {
					model: "fixture-embedding",
					api_key: "fixture-key",
					endpoint: "http://scheduler-fixtures:8080/v1",
				},
			},
		);
		expect(embedding.status).toBe(200);

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
		const globalEditor = page.locator(
			'[aria-label="Global Instructions editor"]',
		);
		await globalEditor.scrollIntoViewIfNeeded();
		await globalEditor.fill(
			"Confirm the customer and summarize any destructive action before execution.",
		);
		await page.getByRole("button", { name: "Save Instructions" }).click();
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
			.getByRole("button", { name: "Edit required instructions" })
			.click();
		const organizationEditor = page.locator(
			'[aria-label="Organization Instructions editor"]',
		);
		await organizationEditor.fill(
			"Use the organization onboarding runbook before provisioning access.",
		);
		await page.getByRole("button", { name: "Save Instructions" }).click();
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

		let memoryId: string | null = null;
		try {
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
			await expect(
				page.getByText("Nothing has been remembered yet."),
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
							content:
								"# Acme onboarding\n\nUse the **Northwind tenant checklist** before provisioning access.",
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
			await expect(page.getByText("Acme onboarding")).toBeVisible();
			await expect(
				page.getByText("Northwind tenant checklist"),
			).toBeVisible();
			await page
				.getByText("Saved Memories", { exact: true })
				.scrollIntoViewIfNeeded();
			await testInfo.attach("Memory management — Saved memory", {
				body: await page.screenshot(),
				contentType: "image/png",
			});

			await page.getByRole("button", { name: "Remove memory" }).click();
			await page.getByRole("button", { name: /^Remove$/ }).click();
			await expect(
				page.getByText("Nothing has been remembered yet."),
			).toBeVisible();
			memoryId = null;
		} finally {
			if (memoryId) {
				await authenticatedJson(page, `/api/memory/${memoryId}`, {
					method: "DELETE",
				});
			}
			await authenticatedJson(page, "/api/memory/settings", {
				method: "PUT",
				body: { enabled: false },
			});
			await authenticatedJson(page, "/api/admin/memory/settings", {
				method: "PUT",
				body: { enabled: false },
			});
			await authenticatedJson(page, "/api/admin/llm/embedding-config", {
				method: "DELETE",
			});
			await authenticatedJson(page, "/api/admin/required-instructions", {
				method: "PUT",
				body: { instructions: "" },
			});
			await authenticatedJson(
				page,
				`/api/admin/required-instructions/organizations/${organizationId}`,
				{ method: "PUT", body: { instructions: "" } },
			);
		}
	});
});
