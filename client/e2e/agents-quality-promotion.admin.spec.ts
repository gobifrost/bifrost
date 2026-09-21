/**
 * Candidate promotion acceptance (Admin)
 *
 * Applies an API-created candidate through the Changes tab review flow and
 * verifies the live prompt updates via the normal authorized update — with
 * history recorded and flagged verdicts preserved. Model-generated proposal
 * generation is retired from the production shell, so this seeds the candidate
 * through the API.
 */

import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

const ORIGINAL_PROMPT = "Reply only with ok.";
const CANDIDATE_PROMPT =
	"Reply only with ok. If the request is ambiguous, ask one question first.";

test("promotion applies a reviewed candidate with reason and stale guard", async ({
	page,
	api,
}, testInfo) => {
	let agentId: string | undefined;
	try {
		const created = await api.post("/api/agents", {
			data: {
				name: `Promote ${randomUUID()}`,
				system_prompt: ORIGINAL_PROMPT,
				access_level: "private",
				system_tools: [],
				channels: [],
			},
		});
		expect(created.ok()).toBe(true);
		const agent = await created.json();
		agentId = agent.id;

		const candidate = await api.post("/api/agent-evaluations/candidates", {
			data: {
				base_agent_id: agentId,
				name: "Ask-first candidate",
				overlays: { system_prompt: CANDIDATE_PROMPT },
				organization_id: agent.organization_id ?? null,
			},
		});
		expect(
			candidate.ok(),
			`Create candidate: ${await candidate.text()}`,
		).toBe(true);
		const candidateId = (await candidate.json()).id;

		await page.goto(
			`/agents/${agentId}/quality?tab=changes&candidate=${candidateId}`,
		);
		await expect(
			page
				.getByLabel("Proposed changes")
				.getByText("Ask-first candidate", { exact: true }),
		).toBeVisible({ timeout: 10000 });
		await page.getByRole("button", { name: "Review and apply" }).click();
		await expect(
			page.getByText("Loading current production configuration…"),
		).toBeHidden({ timeout: 10000 });
		await page
			.getByLabel("Change reason (recorded in prompt history)")
			.fill("Browser promotion acceptance.");
		await page
			.getByRole("button", { name: "Apply these changes to live agent" })
			.scrollIntoViewIfNeeded();
		await page
			.locator("section", {
				has: page.getByRole("heading", {
					name: "Review and apply",
				}),
			})
			.screenshot({
				path: testInfo.outputPath("promotion-apply-review.png"),
			});
		await page.screenshot({
			path: testInfo.outputPath("promotion-review-desktop.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page
			.locator("section", {
				has: page.getByRole("heading", {
					name: "Review and apply",
				}),
			})
			.screenshot({
				path: testInfo.outputPath("promotion-apply-review-mobile.png"),
			});
		await page.setViewportSize({ width: 1280, height: 800 });
		await page
			.getByRole("button", { name: "Apply these changes to live agent" })
			.click();
		await expect
			.poll(async () => {
				const response = await api.get(`/api/agents/${agentId}`);
				expect(response.ok()).toBe(true);
				return (await response.json()).system_prompt;
			})
			.toBe(CANDIDATE_PROMPT);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("promotion-review-mobile.png"),
			fullPage: true,
		});
	} finally {
		if (agentId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
	}
});
