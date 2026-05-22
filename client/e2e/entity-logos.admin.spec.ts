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

import { test, expect } from "./fixtures/api-fixture";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const FIXTURE_PNG = path.join(__dirname, "fixtures", "test-logo.png");

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;

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
				},
			});
			expect(resp.ok(), await resp.text()).toBe(true);
			const app = await resp.json();
			appId = app.id;
		});

		test.afterAll(async ({ api }) => {
			if (appId) await api.delete(`/api/applications/${appId}`);
		});

		test("uploads via the app settings dialog and renders on the card", async ({ page }) => {
			await page.goto(`/apps/${APP_SLUG}/edit`);
			await page.getByRole("button", { name: /^settings$/i }).click();
			await expect(
				page.getByRole("heading", { name: /edit application/i }),
			).toBeVisible();

			// The hidden file input lives inside the logo drop zone.
			const fileInput = page
				.locator('[data-testid="logo-drop-zone"] input[type="file"]');
			await fileInput.setInputFiles(FIXTURE_PNG);

			// Confirmation toast appears
			await expect(page.getByText("Image updated")).toBeVisible();

			// Close the dialog and navigate to the apps list
			await page.keyboard.press("Escape");
			await page.goto("/apps");

			// EntityLogo renders an <img> pointing at the logo endpoint.
			// After a successful upload the server returns 200, so the img
			// stays visible (no onError → no fallback swap).
			const logo = page.locator(
				`img[src*="/api/applications/${appId}/logo"]`,
			);
			await expect(logo).toBeVisible();
		});
	});

	test.describe("Agent logo", () => {
		let agentId: string;

		test.beforeAll(async ({ api }) => {
			const resp = await api.post("/api/agents", {
				data: {
					name: `E2E Logo Bot ${UNIQUE}`,
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
			if (agentId) await api.delete(`/api/agents/${agentId}`);
		});

		test("uploads via the drop zone and renders on the fleet card", async ({ page }) => {
			await page.goto(`/agents/${agentId}`);

			// Wait for the drop zone to be present (it only renders once agent data loads).
			await page.waitForSelector('[data-testid="logo-drop-zone"]');

			// The hidden file input lives inside the drop zone.
			const fileInput = page
				.locator('[data-testid="logo-drop-zone"] input[type="file"]');
			await fileInput.setInputFiles(FIXTURE_PNG);

			await expect(page.getByText("Image updated")).toBeVisible();

			await page.goto("/agents");

			const logo = page.locator(`img[src*="/api/agents/${agentId}/logo"]`);
			await expect(logo).toBeVisible();
		});
	});
});
