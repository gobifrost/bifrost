import { expect, test } from "@playwright/test";

const now = "2026-08-22T12:00:00Z";

test.describe("AI model settings", () => {
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
		const profiles = [
			{
				id: "profile-balanced",
				name: "Balanced",
				connection_id: "connection-default",
				model: "openai/gpt-5-mini",
				max_tokens: 16384,
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
					max_tokens: body.max_tokens,
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
			page.getByRole("heading", { name: "AI Model Settings" }),
		).toBeVisible();
		await expect(
			page.getByText("Profiles required for assignments"),
		).toBeVisible();
		await expect(
			page.getByText("Default", { exact: true }).first(),
		).toBeVisible();
		await page.getByLabel("Provider Name").fill("Additional Provider");
		await page.getByLabel("API Key").fill("sk-test");
		await page.getByRole("button", { name: "Add Provider" }).click();
		await expect
			.poll(() => createdProviderName)
			.toBe("Additional Provider");

		await page.getByLabel("Profile Name").fill("Support Chat");
		await page.getByLabel("Provider Connection").click();
		await page.getByRole("option", { name: /Default/ }).click();
		await page.getByLabel("Model", { exact: true }).fill("gpt-5.1-mini");
		await page
			.getByRole("button", { name: "Create Profile", exact: true })
			.click();
		await expect(
			page.getByText("Support Chat", { exact: true }),
		).toBeVisible();

		const primary = page.getByLabel("Primary Profile");
		await primary.click();
		await page.getByPlaceholder("Search profiles...").fill("Support Chat");
		await page.getByRole("option", { name: /Support Chat/ }).click();
		await expect(primary).toContainText("Support Chat");

		await page.setViewportSize({ width: 1440, height: 1000 });
		await testInfo.attach("AI settings — reusable profiles", {
			body: await page.screenshot({ fullPage: true }),
			contentType: "image/png",
		});
	});
});
