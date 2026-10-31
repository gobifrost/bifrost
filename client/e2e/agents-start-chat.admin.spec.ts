/**
 * Agent Detail — Start Chat flow (admin).
 *
 * Covers the previously-unwired "Start chat" button on AgentDetailPage. The
 * button must create a new conversation bound to the agent and navigate to
 * /chat/:conversationId.
 */

import { test, expect } from "./fixtures/api-fixture";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Detail — Start Chat (admin)", () => {
	test("Start chat button creates a conversation and navigates to /chat", async ({
		page,
	}) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Start Chat Spec",
		});

		await page.goto(`/agents/${agent.id}`);
		const btn = page.getByTestId("start-chat-button");
		await expect(btn).toBeVisible({ timeout: 10000 });
		await expect(btn).toBeEnabled();

		await btn.click();

		// Navigation lands on /chat/:uuid — confirms the mutation fired AND the
		// server returned a new conversation that the route honored.
		await page.waitForURL(/\/chat\/[0-9a-f-]{36}/, { timeout: 15000 });
		expect(page.url()).toMatch(/\/chat\/[0-9a-f-]{36}/);
	});
});
