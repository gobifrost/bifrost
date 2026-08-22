import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { test, expect } from "./fixtures/api-fixture";
import type { AllCredentials } from "./setup/auth-helpers";

const BIFROST_URL = process.env.TEST_BASE_URL || "http://client:80";
const BIFROST_ORIGIN = new URL(BIFROST_URL).origin;
const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;

test.describe.serial("Per-mapping OAuth", () => {
	let integrationId: string;
	let integrationName: string;
	let mappingId: string;
	let organizationName: string;

	test.beforeAll(async ({ api }) => {
		const credentials = JSON.parse(
			readFileSync(resolve("e2e/.auth/credentials.json"), "utf8"),
		) as AllCredentials;

		integrationName = `E2E per-mapping OAuth ${UNIQUE}`;
		organizationName = credentials.org1.name;
		const integration = await api.post("/api/integrations", {
			data: { name: integrationName },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;

		const oauth = await api.post("/api/oauth/connections", {
			data: {
				integration_id: integrationId,
				description: "Playwright per-mapping OAuth fixture",
				oauth_flow_type: "authorization_code",
				client_id: "playwright-client",
				client_secret: "playwright-secret",
				authorization_url: "https://login.example.com/authorize",
				token_url: "https://login.example.com/token",
				scopes: "read,write",
			},
		});
		expect(oauth.ok(), await oauth.text()).toBe(true);

		const mapping = await api.post(
			`/api/integrations/${integrationId}/mappings`,
			{
				data: {
					organization_id: credentials.org1.id,
					entity_id: "playwright-entity",
					entity_name: "Playwright Entity",
				},
			},
		);
		expect(mapping.ok(), await mapping.text()).toBe(true);
		mappingId = ((await mapping.json()) as { id: string }).id;
	});

	test.afterAll(async ({ api }) => {
		if (integrationName) {
			await api.delete(
				`/api/oauth/connections/${encodeURIComponent(integrationName)}`,
			);
		}
		if (integrationId) {
			await api.delete(`/api/integrations/${integrationId}`);
		}
	});

	async function openMappings(page: import("@playwright/test").Page) {
		await page.goto(`/integrations/${integrationId}`);
		await page.getByRole("tab", { name: "Mappings" }).click();
		await expect(
			page.getByRole("columnheader", { name: "Connection" }),
		).toBeVisible();
	}

	test("mapping table renders on integration detail page", async ({
		page,
	}) => {
		await openMappings(page);
		await expect(
			page.getByText(/no data provider configured/i),
		).toBeVisible();
		await expect(page.getByPlaceholder(/entity id/i).first()).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Connect", exact: true }).first(),
		).toBeVisible();
	});

	test("Connect button on mapping row opens authorize URL", async ({
		page,
	}) => {
		await page.route(
			`**/integrations/${integrationId}/mappings/${mappingId}/oauth/authorize`,
			(route) =>
				route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify({
						authorization_url: `${BIFROST_ORIGIN}/oauth-test-authorized?state=test`,
					}),
				}),
		);
		await openMappings(page);

		const authorizeRequest = page.waitForRequest(
			(request) =>
				request.method() === "POST" &&
				request
					.url()
					.endsWith(
						`/integrations/${integrationId}/mappings/${mappingId}/oauth/authorize`,
					),
		);
		const popupPromise = page.waitForEvent("popup");
		await page
			.getByRole("row")
			.filter({ hasText: organizationName })
			.getByRole("button", { name: "Connect", exact: true })
			.click();

		const request = await authorizeRequest;
		expect(request.postDataJSON()).toEqual({
			redirect_uri: `${BIFROST_ORIGIN}/oauth/callback/${integrationId}`,
		});
		const popup = await popupPromise;
		await expect(popup).toHaveURL(
			`${BIFROST_ORIGIN}/oauth-test-authorized?state=test`,
		);
		await popup.close();
	});
});
