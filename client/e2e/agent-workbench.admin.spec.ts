/**
 * Provider-free Agent Workbench journey (Admin).
 *
 * The test seeds a real agent and manual Finding through authenticated API
 * fixtures. It intentionally does not invoke a designer, review, synthetic,
 * or other model provider.
 */
import { randomUUID } from "node:crypto";

import { test, expect } from "./fixtures/api-fixture";

test("turns a fleet Finding into a durable Test without leaving the Workbench", async ({
	page,
	api,
}, testInfo) => {
	const suffix = randomUUID().slice(0, 8);
	const findingDescription = `Atlas must clarify urgent ticket routing ${suffix}.`;
	const expectedBehavior = "Ask one clarifying question before routing.";
	let agentId: string | undefined;

	try {
		const createdAgent = await api.post("/api/agents", {
			data: {
				name: `Atlas Workbench ${suffix}`,
				system_prompt: "Reply only with ok.",
				access_level: "private",
				system_tools: [],
				channels: [],
			},
		});
		expect(createdAgent.ok()).toBe(true);
		agentId = (await createdAgent.json()).id as string;

		const createdFinding = await api.post("/api/agent-findings", {
			data: {
				agent_id: agentId,
				description: findingDescription,
				expected_behavior: expectedBehavior,
			},
		});
		expect(createdFinding.ok()).toBe(true);

		const createdReview = await api.post("/api/agent-reviews", {
			data: {
				agent_id: agentId,
				name: `Find routing improvement opportunities ${suffix}`,
				review_statement:
					"Find recurring routing problems and opportunities in completed runs.",
			},
		});
		expect(createdReview.ok()).toBe(true);

		await page.goto("/agents");
		await page.getByRole("link", { name: "Agent Workbench" }).click();
		await expect(page).toHaveURL(/\/agents\/quality/);

		await expect(
			page.getByRole("button", { name: "Findings", exact: true }),
		).toHaveAttribute("aria-current", "page");
		await page.screenshot({
			path: testInfo.outputPath("agent-workbench-findings-wide.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: findingDescription }).click();
		await expect(
			page.getByRole("heading", { name: "Finding details" }),
		).toBeVisible();

		await page.screenshot({
			path: testInfo.outputPath("agent-workbench-wide.png"),
			fullPage: true,
		});

		await page.getByRole("button", { name: "Create Test" }).click();
		await expect(page).toHaveURL(/collection=tests/);
		await expect(page.getByLabel("Situation")).toHaveValue(
			findingDescription,
		);
		await expect(page.getByLabel("Expected behavior")).toHaveValue(
			expectedBehavior,
		);

		await page
			.getByRole("button", { name: "Create Test", exact: true })
			.click();
		await expect(
			page.getByRole("button", {
				name: "Should ask one clarifying question before routing.",
			}),
		).toBeVisible();
		await expect(page.getByText("Drafting from finding")).toHaveCount(0);
		await expect(page).toHaveURL(/collection=tests/);
		await page.screenshot({
			path: testInfo.outputPath("agent-workbench-tests-wide.png"),
			fullPage: true,
		});

		await page
			.getByRole("button", { name: "Reviews", exact: true })
			.click();
		await expect(
			page.getByText(`Find routing improvement opportunities ${suffix}`),
		).toBeVisible();
		await page.screenshot({
			path: testInfo.outputPath("agent-workbench-reviews-wide.png"),
			fullPage: true,
		});

		await page.getByRole("button", { name: "Tests", exact: true }).click();

		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("agent-workbench-narrow.png"),
			fullPage: true,
		});
	} finally {
		if (agentId) {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
		}
	}
});
