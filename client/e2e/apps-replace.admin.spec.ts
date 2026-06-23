/**
 * Apps Replace Happy Path (Admin)
 *
 * Covers: user opens an app's settings, expands the Advanced section, clicks
 * Replace, picks a new folder, and sees the inline validation results. This
 * is the end-to-end wiring test — component-level logic (warnings, phases)
 * is covered by AppReplacePathDialog.test.tsx, and the CLI/REST contract is
 * covered by api/tests/e2e/platform/test_cli_apps_replace.py.
 */

import { test, expect, grantWorkspaceAppPolicy } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const APP_SLUG = `e2e-replace-${UNIQUE}`;
const APP_NAME = `E2E Replace ${UNIQUE}`;
const ORIGIN_PATH = `apps/${APP_SLUG}`;
const TARGET_PATH = `apps/${APP_SLUG}-v2`;

test.describe("Apps Replace", () => {
	let appId: string;

	test.beforeAll(async ({ api }) => {
		const response = await api.post("/api/applications", {
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
		expect(response.ok(), await response.text()).toBe(true);
		const app = await response.json();
		appId = app.id;
		await grantWorkspaceAppPolicy(api, APP_SLUG);
		expect(app.repo_path).toBe(ORIGIN_PATH);
	});

	test.afterAll(async ({ api }) => {
		if (appId) await api.delete(`/api/applications/${appId}`);
	});

	test("replaces an app's path via the Advanced section", async ({ page }) => {
		await page.goto(`/apps/${APP_SLUG}/edit`);

		// Open the settings (gear) button — this opens AppInfoDialog.
		// Exact match to avoid colliding with the nearby "Embed settings" button.
		await page.getByRole("button", { name: /^settings$/i }).click();
		await expect(
			page.getByRole("heading", { name: /edit application/i }),
		).toBeVisible();

		// Expand the Advanced section. It's collapsed by default and is the
		// ONLY entry point to Replace — this assertion also guards that rule.
		await page.getByRole("button", { name: /^advanced$/i }).click();
		await expect(page.getByText("Source path")).toBeVisible();

		// Current path is visible, matches what we created.
		await expect(
			page.getByText(ORIGIN_PATH, { exact: true }),
		).toBeVisible();

		// Open the Replace dialog.
		await page.getByRole("button", { name: /replace…/i }).click();
		await expect(
			page.getByRole("heading", { name: /replace app path/i }),
		).toBeVisible();

		// Type the new path. We force through the source-exists check because
		// the target folder doesn't exist yet — matches the CLI's --force semantics.
		await page.getByLabel(/new path/i).fill(TARGET_PATH);

		await page.getByRole("button", { name: /^advanced$/i }).click();
		await page
			.getByRole("checkbox", { name: /force/i })
			.check();

		await page.getByRole("button", { name: /^replace$/i }).click();

		// Validation phase: either "No issues found" (clean app) or an Errors/Warnings
		// panel. Either way, we should see the post-replace footer.
		await expect(
			page.getByRole("button", { name: "Close", exact: true }),
		).toBeVisible({ timeout: 15000 });
		await expect(
			page.getByRole("button", { name: /open app/i }),
		).toBeVisible();
	});
});
