import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("durable evidence lives in run detail advanced and keeps run identity", async ({
	page,
	api,
}, testInfo) => {
	let agentId: string | undefined;
	try {
		const created = await api.post("/api/agents", {
			data: {
				name: `Evidence ${randomUUID()}`,
				system_prompt: "Reply only with ok.",
				access_level: "private",
				system_tools: [],
				channels: [],
			},
		});
		expect(created.ok()).toBe(true);
		const agent = await created.json();
		agentId = agent.id;
		const queued = await api.post("/api/agent-runs/enqueue", {
			data: {
				agent_name: agent.name,
				input: { message: "Return the fixture answer." },
			},
		});
		expect(queued.ok()).toBe(true);
		const { run_id } = await queued.json();
		await page.goto(`/agents/${agentId}/runs/${run_id}`);
		const finished = page.waitForResponse(async (response) => {
			if (
				!response
					.url()
					.endsWith(`/api/agent-runs/${run_id}/snapshot`) ||
				!response.ok()
			)
				return false;
			return [
				"completed",
				"failed",
				"contract_failed",
				"timeout",
			].includes((await response.json()).status);
		});
		await page.goto(`/agents/${agentId}/runs/${run_id}?tab=activity`);
		await expect(page).toHaveURL(/tab=activity/);
		await page.getByRole("button", { name: "Advanced" }).click();
		await expect(
			page.getByRole("heading", { name: "Journal timeline" }),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Saved run snapshot" }),
		).toBeVisible();
		await expect(
			page.getByText("Prompt SHA-256:", { exact: false }),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Checkpoints", exact: true }),
		).toBeVisible();
		await finished;
		await page.screenshot({
			path: testInfo.outputPath("run-evidence-desktop.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("run-evidence-mobile.png"),
			fullPage: true,
		});
		// The standalone debugger route is gone; the run stays readable.
		await page.goto(`/agents/${agentId}/runs/${run_id}`);
		await expect(
			page.getByTestId("agent-run-detail-page"),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Run debugger" }),
		).toHaveCount(0);
	} finally {
		if (agentId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
	}
});
