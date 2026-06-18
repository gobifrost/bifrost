/**
 * Chat V2 — M6 DELEGATION badge browser verification (DEBUG stack).
 *
 * The "✓ consulted <agent>" badge (DelegationBadge, a Radix Popover) is set
 * live from delegation_started/complete chunks AND, after this session's fix,
 * reconstructed onto MessagePublic so it survives a reload. This drives the
 * reload path in real Chromium against a seeded delegation conversation.
 *
 * HOW TO RUN (debug stack URL from `./debug.sh status`):
 *   cd client && TEST_BASE_URL=http://localhost:32944 \
 *     npx playwright test e2e/chat-delegation.debug.spec.ts \
 *     --project=chromium --no-deps --reporter=list
 */

import { test, expect } from "@playwright/test";

// A seeded conversation where "Concierge" delegated to "Weather Specialist".
const CONVERSATION_ID = "a02d1042-fddb-4a73-b046-2aeb0044a787";
const DEV_EMAIL = "dev@gobifrost.com";
const DEV_PASSWORD = "password";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Chat V2 delegation (debug stack)", () => {
	test.beforeEach(async ({ page }) => {
		await page.goto("/login", { waitUntil: "domcontentloaded" });
		await expect(page.getByLabel("Email")).toBeVisible({ timeout: 15000 });
		await page.getByLabel("Email").fill(DEV_EMAIL);
		await page.getByLabel("Password").fill(DEV_PASSWORD);
		await page
			.getByRole("button", { name: "Sign In", exact: true })
			.click();
		await page.waitForURL((u) => !u.pathname.includes("/login"), {
			timeout: 20000,
		});
	});

	test("the consulted badge renders on reload and expands the exchange", async ({
		page,
	}) => {
		await page.goto(`/chat/${CONVERSATION_ID}`, {
			waitUntil: "domcontentloaded",
		});

		// The "✓ consulted Weather Specialist" badge — reconstructed from the
		// persisted delegate_to_* message (no live chunks on a cold load).
		const badge = page.getByTestId("delegation-badge").first();
		await expect(badge).toBeVisible({ timeout: 15000 });
		await expect(badge).toContainText(/consulted/i);
		await expect(badge).toContainText(/Weather Specialist/i);

		// Expand the Popover detail (the delegated agent's task + response).
		await badge.click();
		await expect(
			page.getByText(/weather in Paris/i).first(),
		).toBeVisible({ timeout: 5000 });

		await test.info().attach("delegation-badge-expanded", {
			body: await page.screenshot(),
			contentType: "image/png",
		});
	});
});
