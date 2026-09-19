import { randomUUID } from "node:crypto";
import { test, expect } from "./fixtures/api-fixture";

test("debugger inspects a durable run and returns to its original detail", async ({
	page,
	api,
}, testInfo) => {
	let agentId: string | undefined;
	try {
		const created = await api.post("/api/agents", {
			data: {
				name: `Debugger ${randomUUID()}`,
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
		await page.getByRole("link", { name: "Open debugger" }).click();
		await expect(
			page.getByRole("heading", { name: "Run debugger" }),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Immutable snapshot" }),
		).toBeVisible();
		await expect(page.getByLabel("Delegated runs")).toBeVisible();
		await expect(
			page.getByText("Prompt SHA-256:", { exact: false }),
		).toBeVisible();
		await expect(
			page.getByRole("heading", { name: "Checkpoints", exact: true }),
		).toBeVisible();
		await finished;
		await page.screenshot({
			path: testInfo.outputPath("debugger-desktop.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("debugger-mobile.png"),
			fullPage: true,
		});
		await page.getByRole("link", { name: "Back to run" }).click();
		await expect(page).toHaveURL(new RegExp(`/runs/${run_id}$`));
	} finally {
		if (agentId)
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/agents/${agentId}`)).status(),
			);
	}
});
