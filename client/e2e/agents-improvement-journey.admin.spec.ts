/**
 * Agent quality journey (Admin): reviewed run → finding → durable test.
 *
 * The test stack uses its local model fixture for the source chat run. The
 * quality workbench itself performs no model call in this journey.
 */
import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test.setTimeout(120000);

test("reviewed finding becomes a durable test without losing source evidence", async ({
	page,
	api,
}, testInfo) => {
	const name = `Atlas Journey ${randomUUID().slice(0, 8)}`;
	let agentId: string | undefined;
	let runId = "";
	try {
		const created = await api.post("/api/agents", {
			data: {
				name,
				system_prompt: "Reply only with ok.",
				access_level: "private",
				system_tools: [],
				channels: [],
			},
		});
		expect(created.ok()).toBe(true);
		agentId = (await created.json()).id;

		const conversation = await api.post("/api/chat/conversations", {
			data: { agent_id: agentId, title: name, channel: "chat" },
		});
		expect(conversation.ok()).toBe(true);
		const started = await api.post("/api/chat/runs", {
			data: {
				conversation_id: (await conversation.json()).id,
				content: "Return the fixture answer.",
				attachment_ids: [],
			},
		});
		expect(started.ok()).toBe(true);
		await expect
			.poll(
				async () => {
					const response = await api.get("/api/agent-runs", {
						params: { agent_id: agentId! },
					});
					expect(response.ok()).toBe(true);
					const runs = (await response.json()).items as Array<{
						id: string;
						status: string;
					}>;
					if (runs[0]) runId = runs[0].id;
					return runs[0]?.status;
				},
				{ timeout: 60000 },
			)
			.toBe("completed");

		const flagged = await api.post(`/api/agent-runs/${runId}/verdict`, {
			data: { verdict: "down", note: "Needs review" },
		});
		expect(flagged.ok()).toBe(true);
		await page.goto(`/agents/${agentId}/review`);
		await page
			.getByRole("button", { name: "Record finding from this run" })
			.click();
		await expect(page.getByText("Finding recorded")).toBeVisible();

		const findingsResponse = await api.get("/api/agent-findings", {
			params: { agent_id: agentId },
		});
		expect(findingsResponse.ok()).toBe(true);
		const findings = (await findingsResponse.json()) as Array<{
			id: string;
			description: string;
		}>;
		expect(findings).toHaveLength(1);

		await page.goto(
			`/agents/${agentId}/quality?collection=findings&selected=findings%3A${findings[0].id}`,
		);
		await expect(
			page.getByRole("heading", { name: "Finding details" }),
		).toBeVisible({ timeout: 10000 });
		await expect(page.getByRole("link", { name: `Run ${runId}` })).toBeVisible();
		await page.screenshot({
			path: testInfo.outputPath("finding-inspector-desktop.png"),
			fullPage: true,
		});
		await page.getByRole("button", { name: "Create test from finding" }).click();
		await expect(page).toHaveURL(/collection=tests/);
		await expect(page.getByText("Drafting from finding")).toBeVisible();
		await page.getByLabel("Situation").fill("When a response is ambiguous");
		await page
			.getByLabel("Expected behavior")
			.fill("Ask one clarifying question");
		await page.getByText("Advanced JSON/checks").click();
		await page
			.getByLabel("Advanced JSON")
			.fill('{"forbidden_tools":["delete_record"],"tags":["reviewed"]}');
		await page.getByRole("button", { name: "Create test" }).click();
		await expect(
			page.getByText("Should ask one clarifying question", { exact: true }),
		).toBeVisible({ timeout: 10000 });

		const testsResponse = await api.get(
			`/api/agent-evaluations/agents/${agentId}/tests`,
		);
		expect(testsResponse.ok()).toBe(true);
		const tests = (await testsResponse.json()).items as Array<{
			finding_id: string | null;
			forbidden_tools: string[];
			tags: string[];
		}>;
		expect(tests[0]).toMatchObject({
			finding_id: findings[0].id,
			forbidden_tools: ["delete_record"],
			tags: ["reviewed"],
		});

		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("durable-test-mobile.png"),
			fullPage: true,
		});
		const reviewed = await (await api.get(`/api/agent-runs/${runId}`)).json();
		expect(reviewed.verdict).toBe("down");
		expect(reviewed.verdict_note).toBe("Needs review");
	} finally {
		if (agentId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
	}
});
