import { expect, test } from "@playwright/test";

const now = "2026-08-22T12:00:00Z";

test.describe("AI model settings", () => {
	test("selects an embedding model from the chosen provider", async ({
		page,
	}) => {
		let savedEmbedding:
			{ connection_id: string; model: string } | undefined;
		await page.route("**/api/admin/ai/connections", async (route) => {
			await route.fulfill({
				json: [
					{
						id: "connection-default",
						name: "Default",
						provider: "openai",
						endpoint: "https://api.openai.com/v1",
						api_key_set: true,
						profile_count: 0,
						created_at: now,
						updated_at: now,
					},
				],
			});
		});
		await page.route(
			"**/api/admin/ai/connections/*/models",
			async (route) => {
				await route.fulfill({
					json: {
						provider: "openai",
						models: [
							{
								id: "text-embedding-3-large",
								display_name: "Text Embedding 3 Large",
								output_modalities: ["text"],
							},
						],
					},
				});
			},
		);
		await page.route("**/api/admin/llm/embedding-config", async (route) => {
			if (route.request().method() === "POST") {
				const body = route.request().postDataJSON();
				savedEmbedding = {
					connection_id: body.connection_id,
					model: body.model,
				};
				await route.fulfill({
					json: {
						saved: true,
						needs_reindex_confirmation: false,
						notification_id: null,
					},
				});
				return;
			}
			await route.fulfill({ json: null });
		});

		await page.goto("/settings/ai-embeddings");
		await expect(
			page.getByRole("heading", { name: "Embeddings" }),
		).toBeVisible();
		await expect(page.getByLabel("Model")).toBeDisabled();
		await page.getByLabel("Provider connection").click();
		await page.getByRole("option", { name: /Default/ }).click();
		await page.getByLabel("Model").click();
		await page
			.getByRole("option", { name: /Text Embedding 3 Large/ })
			.click();
		await page.getByRole("button", { name: "Save embeddings" }).click();

		await expect
			.poll(() => savedEmbedding)
			.toEqual({
				connection_id: "connection-default",
				model: "text-embedding-3-large",
			});
	});

	test("creates reusable provider and model profiles and assigns them", async ({
		page,
	}, testInfo) => {
		const connections = [
			{
				id: "connection-default",
				name: "Default",
				provider: "openrouter",
				endpoint: "https://openrouter.ai/api/v1",
				api_key_set: true,
				profile_count: 1,
				created_at: now,
				updated_at: now,
			},
		];
		let createdProviderName: string | null = null;
		let providerVerified = false;
		const profiles = [
			{
				id: "profile-balanced",
				name: "Balanced",
				connection_id: "connection-default",
				model: "openai/gpt-5-mini",
				capabilities: null,
				enabled_for_chat: true,
				connection: {
					id: "connection-default",
					name: "Default",
					provider: "openrouter",
					endpoint: "https://openrouter.ai/api/v1",
				},
				assignment_keys: ["chat_default"],
				referenced_agent_count: 0,
				created_at: now,
				updated_at: now,
			},
		];
		const assignments = [
			{
				assignment_key: "chat_default",
				profile_id: "profile-balanced",
				profile: profiles[0],
				created_at: now,
				updated_at: now,
			},
		];

		await page.route(
			"**/api/admin/ai/connections/verify",
			async (route) => {
				providerVerified = true;
				await route.fulfill({
					json: {
						success: true,
						message: "Connected",
						models: [
							{
								id: "gpt-5.1-mini",
								display_name: "GPT-5.1 mini",
								output_modalities: ["text"],
							},
						],
					},
				});
			},
		);
		await page.route(
			"**/api/admin/ai/connections/*/models",
			async (route) => {
				await route.fulfill({
					json: {
						provider: "openai",
						models: [
							{
								id: "gpt-5.1-mini",
								display_name: "GPT-5.1 mini",
								output_modalities: ["text"],
							},
						],
					},
				});
			},
		);

		await page.route("**/api/admin/ai/connections", async (route) => {
			if (route.request().method() === "POST") {
				const body = route.request().postDataJSON();
				createdProviderName = body.name;
				connections.push({
					id: "connection-added",
					name: body.name,
					provider: body.provider,
					endpoint: body.endpoint,
					api_key_set: true,
					profile_count: 0,
					created_at: now,
					updated_at: now,
				});
				await route.fulfill({ status: 201, json: connections.at(-1) });
				return;
			}
			await route.fulfill({ json: connections });
		});
		await page.route("**/api/admin/ai/profiles", async (route) => {
			if (route.request().method() === "POST") {
				const body = route.request().postDataJSON();
				const connection = connections.find(
					(item) => item.id === body.connection_id,
				)!;
				profiles.push({
					id: "profile-support",
					name: body.name,
					connection_id: body.connection_id,
					model: body.model,
					capabilities: null,
					enabled_for_chat: body.enabled_for_chat,
					connection: {
						id: connection.id,
						name: connection.name,
						provider: connection.provider,
						endpoint: connection.endpoint,
					},
					assignment_keys: [],
					referenced_agent_count: 0,
					created_at: now,
					updated_at: now,
				});
				await route.fulfill({ status: 201, json: profiles.at(-1) });
				return;
			}
			await route.fulfill({ json: profiles });
		});
		await page.route("**/api/admin/ai/assignments**", async (route) => {
			if (route.request().method() === "PUT") {
				const body = route.request().postDataJSON();
				const key = route.request().url().split("/").at(-1)!;
				const profile = profiles.find(
					(item) => item.id === body.profile_id,
				)!;
				const assignment = {
					assignment_key: key,
					profile_id: profile.id,
					profile,
					created_at: now,
					updated_at: now,
				};
				const index = assignments.findIndex(
					(item) => item.assignment_key === key,
				);
				if (index >= 0) assignments[index] = assignment;
				else assignments.push(assignment);
				await route.fulfill({ json: assignment });
				return;
			}
			await route.fulfill({ json: assignments });
		});

		await page.goto("/settings/ai");
		await expect(
			page.getByRole("heading", { name: "Models" }),
		).toBeVisible();
		await expect(
			page.getByText("Profiles required for assignments"),
		).toBeVisible();
		await expect(
			page.getByText("Default", { exact: true }).first(),
		).toBeVisible();
		await page.getByRole("button", { name: "Add Provider" }).click();
		const providerDialog = page.getByRole("dialog");
		await providerDialog
			.getByLabel("Connection Name")
			.fill("Additional Provider");
		await providerDialog.getByLabel("API Key").fill("sk-test");
		await providerDialog
			.getByRole("button", { name: "Add Provider" })
			.click();
		await expect
			.poll(() => createdProviderName)
			.toBe("Additional Provider");
		expect(providerVerified).toBe(true);

		await page.getByRole("button", { name: "Add Profile" }).click();
		const profileDialog = page.getByRole("dialog");
		await profileDialog.getByLabel("Profile Name").fill("Support Chat");
		await profileDialog.getByLabel("Provider Connection").click();
		await page.getByRole("option", { name: /Default/ }).click();
		await profileDialog.getByLabel("Model", { exact: true }).click();
		await page
			.getByRole("option", { name: "GPT-5.1 mini gpt-5.1-mini" })
			.click();
		await profileDialog
			.getByRole("button", { name: "Add Profile", exact: true })
			.click();
		await expect
			.poll(() =>
				profiles.some((profile) => profile.name === "Support Chat"),
			)
			.toBe(true);
		await expect(
			page.getByRole("button", { name: "Edit Support Chat" }),
		).toBeVisible();
		const supportProfileCard = page
			.locator('[data-slot="card"]')
			.filter({ hasText: "Support Chat" })
			.first();
		await supportProfileCard
			.getByRole("button", { name: "Set Default" })
			.click();
		await expect(
			supportProfileCard.getByRole("button", { name: "Default" }),
		).toBeVisible();
		await expect(supportProfileCard.getByRole("switch")).not.toBeChecked();

		await expect(
			page.getByLabel("Default Profile", { exact: true }),
		).toContainText("Support Chat");

		await page.setViewportSize({ width: 1440, height: 1000 });
		await page
			.getByRole("heading", { name: "Models" })
			.scrollIntoViewIfNeeded();
		await testInfo.attach("AI settings — reusable profiles", {
			body: await page.screenshot({ fullPage: true }),
			contentType: "image/png",
		});
	});
});
