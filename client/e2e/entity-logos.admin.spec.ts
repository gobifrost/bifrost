/**
 * Entity Logos Happy Path (Admin)
 *
 * Covers: an admin uploads a square logo for an app via the settings dialog
 * and for an agent via the detail-page drop zone. Verifies the rendered
 * <img> on each card after upload.
 *
 * Component-level logic is covered by EntityLogo/LogoDropZone vitest.
 * API round-trip is covered by api/tests/e2e/api/test_entity_logos.py.
 * This spec is the wire-up test.
 */

import { test, expect, grantWorkspaceAppPolicy } from "./fixtures/api-fixture";

const FIXTURE_PNG = {
	name: "test-logo.png",
	mimeType: "image/png",
	buffer: Buffer.from(
		"iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGOUm/D/PwMDAwMTAxQAACgXArDsKm8qAAAAAElFTkSuQmCC",
		"base64",
	),
};

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;

async function uploadLogo(page: import("./fixtures/api-fixture").Page, name: string) {
	const uploadZone = page.getByRole("group", { name });
	await expect(uploadZone).toBeVisible();
	await uploadZone.locator('input[type="file"]').setInputFiles(FIXTURE_PNG);
	await expect(page.getByText("Image updated")).toBeVisible();
}

test.describe("Entity logos", () => {
	test.describe("App logo", () => {
		const APP_SLUG = `e2e-logo-${UNIQUE}`;
		const APP_NAME = `E2E Logo ${UNIQUE}`;
		let appId: string;

		test.beforeAll(async ({ api }) => {
			const resp = await api.post("/api/applications", {
				data: {
					name: APP_NAME,
					slug: APP_SLUG,
					access_level: "authenticated",
					role_ids: [],
					// `POST /api/applications` now defaults to standalone_v2 (which requires
					// a Solution deploy); pin the legacy inline model this suite relies on.
					app_model: "inline_v1",
				},
			});
			expect(resp.ok(), await resp.text()).toBe(true);
			const app = await resp.json();
			appId = app.id;
			await grantWorkspaceAppPolicy(api, APP_SLUG);
		});

		test.afterAll(async ({ api }) => {
			if (!appId) return;
			const resp = await api.delete(`/api/applications/${appId}`);
			expect(resp.ok(), await resp.text()).toBe(true);
		});

		test("uploads via the app settings dialog and renders on the list row", async ({
			page,
		}) => {
			await page.goto(`/apps/${APP_SLUG}/edit`);
			await page.getByRole("button", { name: /^settings$/i }).click();
			await expect(
				page.getByRole("heading", { name: /edit application/i }),
			).toBeVisible();

			await uploadLogo(page, "Upload image (click or drag)");

			// Close the dialog and navigate to the apps list
			await page.keyboard.press("Escape");
			await page.goto("/apps");
			await page.getByRole("radio", { name: "Table view" }).click();

			const row = page.getByRole("row", {
				name: new RegExp(APP_NAME),
			});
			const logo = row.locator("img");
			await expect(logo).toBeVisible();
			await expect(logo).toHaveAttribute(
				"src",
				new RegExp(
					`^/api/applications/${appId}/logo\\?v=[0-9a-f]{64}$`,
				),
			);
		});
	});

	test.describe("Agent logo", () => {
		const AGENT_NAME = `E2E Logo Bot ${UNIQUE}`;
		let agentId: string;

		test.beforeAll(async ({ api }) => {
			const resp = await api.post("/api/agents", {
				data: {
					name: AGENT_NAME,
					system_prompt: "You are an e2e helper.",
					channels: ["chat"],
					access_level: "authenticated",
				},
			});
			expect(resp.ok(), await resp.text()).toBe(true);
			const agent = await resp.json();
			agentId = agent.id;
		});

		test.afterAll(async ({ api }) => {
			if (!agentId) return;
			const resp = await api.delete(`/api/agents/${agentId}`);
			expect(resp.ok(), await resp.text()).toBe(true);
		});

		test("uploads via the drop zone and renders on the fleet row", async ({
			page,
		}) => {
			await page.goto(`/agents/${agentId}`);

			await page.getByRole("button", { name: "Edit agent logo" }).click();
			await expect(
				page.getByRole("heading", { name: "Agent logo" }),
			).toBeVisible();
			await uploadLogo(page, "Upload agent logo");
			await page.getByRole("button", { name: "Done" }).click();

			const perAgentStatsRequests: string[] = [];
			page.on("request", (request) => {
				if (
					/\/api\/agents\/[0-9a-f-]+\/stats$/.test(
						new URL(request.url()).pathname,
					)
				) {
					perAgentStatsRequests.push(request.url());
				}
			});
			const listWithStats = page.waitForResponse((response) => {
				const url = new URL(response.url());
				return (
					url.pathname === "/api/agents" &&
					url.searchParams.get("include_stats") === "true"
				);
			});

			await page.goto("/agents");
			await page.getByRole("button", { name: "Table view" }).click();
			await listWithStats;

			const row = page.getByRole("row", {
				name: new RegExp(AGENT_NAME),
			});
			const logo = row.locator("img");
			await expect(logo).toBeVisible();
			await expect(logo).toHaveAttribute(
				"src",
				new RegExp(`^/api/agents/${agentId}/logo\\?v=[0-9a-f]{64}$`),
			);
			expect(perAgentStatsRequests).toEqual([]);
		});
	});
});
