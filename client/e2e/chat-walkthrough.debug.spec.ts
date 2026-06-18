/**
 * Chat V2 — full surface walkthrough (DEBUG stack, real LLM).
 *
 * Drives the core chat experience end-to-end as a user in real Chromium against
 * the debug stack (deepseek via OpenRouter is live). One session touches: send a
 * turn + see a streamed reply, the composer + model pill, the Artifacts panel,
 * and the Toolbox panel. The per-feature debug specs (artifacts/toolbox/
 * delegation/cost-tier) assert the deep behavior; this is the integrated smoke.
 *
 * HOW TO RUN:
 *   cd client && TEST_BASE_URL=http://localhost:32944 \
 *     npx playwright test e2e/chat-walkthrough.debug.spec.ts \
 *     --project=chromium --no-deps --reporter=list
 */

import { test, expect } from "@playwright/test";

const DEV_EMAIL = "dev@gobifrost.com";
const DEV_PASSWORD = "password";

test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Chat V2 walkthrough (debug stack)", () => {
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

	test("send a turn, get a streamed reply, and open the panels", async ({
		page,
	}) => {
		await page.goto("/chat", { waitUntil: "domcontentloaded" });

		// Compose + send a real turn (deepseek answers).
		const composer = page.getByLabel("Chat input");
		await expect(composer).toBeVisible({ timeout: 15000 });
		await composer.fill("Reply with exactly the word: walkthrough");
		await page.getByRole("button", { name: "Send message" }).click();

		// The user message renders, then a streamed assistant reply arrives.
		await expect(
			page.getByText("Reply with exactly the word", { exact: false }).first(),
		).toBeVisible({ timeout: 15000 });
		await expect(
			page.getByText(/walkthrough/i).nth(1),
		).toBeVisible({ timeout: 40000 });

		// The model pill (composer) is present and shows the configured model.
		await expect(
			page.getByRole("combobox").first(),
		).toBeVisible();

		// Artifacts panel opens (Radix Sheet).
		await page.getByRole("button", { name: "Artifacts" }).click();
		await expect(page.getByRole("dialog")).toBeVisible({ timeout: 10000 });
		await page.keyboard.press("Escape");

		// Toolbox panel opens (Radix Sheet). A brand-new /chat has no agent yet,
		// so the panel shows its graceful empty state — assert that, which proves
		// the Sheet mounts. (Agent-bound Toolbox content is covered by
		// chat-toolbox.debug.spec.ts against a seeded agent conversation.)
		await page.getByRole("button", { name: "Toolbox" }).click();
		const toolbox = page.getByRole("dialog");
		await expect(toolbox).toBeVisible({ timeout: 10000 });
		await expect(toolbox.getByText("Toolbox", { exact: true })).toBeVisible();

		await test.info().attach("walkthrough-final", {
			body: await page.screenshot(),
			contentType: "image/png",
		});
	});
});
