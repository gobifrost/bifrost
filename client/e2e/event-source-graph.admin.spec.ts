import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./fixtures/api-fixture";
import type { AllCredentials } from "./setup/auth-helpers";

test.describe.serial("Microsoft Graph event source", () => {
	let integrationId: string;
	let organizationId: string;
	let organizationName: string;

	test.beforeAll(async ({ api }) => {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as AllCredentials;
		organizationId = credentials.org1.id;
		organizationName = credentials.org1.name;

		const integration = await api.post("/api/integrations", {
			data: { name: "Microsoft" },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;

		const oauth = await api.post("/api/oauth/connections", {
			data: {
				integration_id: integrationId,
				oauth_flow_type: "client_credentials",
				client_id: "playwright-client",
				client_secret: "playwright-secret",
				token_url:
					"https://login.microsoftonline.com/{entity_id}/oauth2/v2.0/token",
				scopes: "https://graph.microsoft.com/.default",
			},
		});
		expect(oauth.ok(), await oauth.text()).toBe(true);

		const mapping = await api.post(
			`/api/integrations/${integrationId}/mappings`,
			{
				data: {
					organization_id: organizationId,
					entity_id: "playwright-tenant",
					entity_name: "Playwright tenant",
				},
			},
		);
		expect(mapping.ok(), await mapping.text()).toBe(true);
	});

	test.afterAll(async ({ api }) => {
		if (integrationId) {
			await api.delete("/api/oauth/connections/Microsoft");
			await api.delete(`/api/integrations/${integrationId}`);
		}
	});

	test("loads the mapped tenant's users after a visible retry", async ({
		page,
	}) => {
		const requests: Array<Record<string, unknown>> = [];
		let tenantAvailable = false;
		await page.route(
			"**/api/events/adapters/microsoft_graph/dynamic-values",
			async (route) => {
				requests.push(route.request().postDataJSON());
				if (!tenantAvailable) {
					await route.fulfill({
						status: 502,
						contentType: "application/json",
						body: JSON.stringify({
							detail: "Tenant token unavailable",
						}),
					});
					return;
				}
				await route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify({
						items: [
							{
								id: "user-1",
								display_name: "Adele Vance",
								user_principal_name: "adele@example.com",
								label: "adele@example.com · Adele Vance",
							},
						],
					}),
				});
			},
		);

		await page.goto("/event-sources");
		await page
			.getByRole("button", { name: "Create Event Source" })
			.first()
			.click();
		const dialog = page.getByRole("dialog", {
			name: "Create Event Source",
		});

		await dialog.getByRole("combobox").first().click();
		await page.getByRole("option", { name: organizationName }).click();
		await dialog.getByRole("combobox", { name: "Webhook Adapter" }).click();
		await page.getByRole("option", { name: "Microsoft Graph" }).click();
		await dialog.getByRole("combobox", { name: "Integration" }).click();
		await page.getByRole("option", { name: "Microsoft" }).click();

		await expect(dialog.getByText("Options did not load.")).toBeVisible();
		await expect(
			dialog.getByRole("button", { name: "Retry" }),
		).toBeVisible();
		tenantAvailable = true;
		await dialog.getByRole("button", { name: "Retry" }).click();

		await expect(dialog.getByText("Options did not load.")).toBeHidden();
		await expect(dialog.getByText("Select User...")).toBeVisible();
		await dialog.getByText("Select User...").click();
		await expect(
			page.getByRole("option", {
				name: "adele@example.com · Adele Vance",
			}),
		).toBeVisible();
		expect(requests.length).toBeGreaterThanOrEqual(2);
		for (const request of requests) {
			expect(request).toMatchObject({
				operation: "list_users",
				integration_id: integrationId,
				organization_id: organizationId,
			});
		}
	});

	test("shows Graph context and recreates the provider subscription", async ({
		page,
	}) => {
		const sourceId = "11111111-1111-4111-8111-111111111111";
		const source = {
			id: sourceId,
			name: "Adele inbox changes",
			description: null,
			source_type: "webhook",
			organization_id: organizationId,
			organization_name: organizationName,
			is_active: true,
			event_type: null,
			webhook: {
				adapter_name: "microsoft_graph",
				integration_id: integrationId,
				integration_name: "Microsoft",
				config: {
					user_id: "user-1",
					resource: "/users/user-1/messages",
					change_types: ["created"],
				},
				callback_url: `/api/events/webhook/${sourceId}`,
				external_id: "graph-subscription-1",
				provider_metadata: {
					provider: "microsoft_graph",
					user_id: "user-1",
					user_display_name: "Adele Vance",
					user_principal_name: "adele@example.com",
					resource: "/users/user-1/messages",
					change_types: ["created"],
				},
				expires_at: "2099-08-28T00:00:00Z",
				rate_limit_per_minute: 60,
				rate_limit_window_seconds: 60,
				rate_limit_enabled: true,
				rate_limited_count_24h: 0,
			},
			schedule: null,
			subscription_count: 0,
			event_count_24h: 1,
			error_message: null,
			created_at: "2026-08-27T20:00:00Z",
			updated_at: "2026-08-27T20:00:00Z",
		};
		let resubscribeRequests = 0;

		await page.route(/\/api\/events\/sources(?:\?.*)?$/, async (route) => {
			await route.fulfill({
				status: 200,
				contentType: "application/json",
				body: JSON.stringify({ items: [source], total: 1 }),
			});
		});
		await page.route(
			new RegExp(`/api/events/sources/${sourceId}/resubscribe$`),
			async (route) => {
				resubscribeRequests += 1;
				await route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify(source),
				});
			},
		);
		await page.route(
			new RegExp(`/api/events/sources/${sourceId}$`),
			async (route) => {
				await route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify(source),
				});
			},
		);
		await page.route(
			new RegExp(`/api/events/sources/${sourceId}/events(?:\\?.*)?$`),
			async (route) => {
				await route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify({
						items: [
							{
								id: "22222222-2222-4222-8222-222222222222",
								event_source_id: sourceId,
								event_source_name: source.name,
								event_type: "01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created",
								received_at: "2026-08-27T20:30:00Z",
								headers: {},
								data: { change_type: "created" },
								source_ip: "10.0.0.1",
								status: "completed",
								delivery_count: 0,
								success_count: 0,
								failed_count: 0,
								created_at: "2026-08-27T20:30:00Z",
							},
						],
						total: 1,
					}),
				});
			},
		);

		await page.goto("/event-sources");
		await expect(page.getByText("Adele inbox changes")).toBeVisible();
		await expect(
			page.getByText("adele@example.com · Mail messages · created"),
		).toBeVisible();
		await expect(page.getByText("Microsoft Graph")).toBeVisible();

		await page.getByText("Adele inbox changes").click();
		await expect(
			page.getByRole("heading", { name: "Microsoft Graph" }),
		).toBeVisible();
		await expect(page.getByText("adele@example.com")).toBeVisible();
		await expect(page.getByText("Connected")).toBeVisible();
		await page.getByRole("tab", { name: /Events/ }).click();
		await expect(page.getByText("graph.messages.created")).toBeVisible();
		await expect(
			page.getByText("01V6T7ZK0M0Q8SHJ4A1N5W2X9B.created"),
		).toBeHidden();

		await page.getByRole("button", { name: "Resubscribe" }).click();
		const confirmation = page.getByRole("alertdialog", {
			name: "Resubscribe to Microsoft Graph?",
		});
		await confirmation.getByRole("button", { name: "Resubscribe" }).click();
		await expect.poll(() => resubscribeRequests).toBe(1);
		await expect(
			page.getByText("Microsoft Graph subscription recreated"),
		).toBeVisible();
	});
});
