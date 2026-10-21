import { randomUUID } from "node:crypto";
import type { components } from "../src/lib/v1";
import { test, expect } from "./fixtures/api-fixture";

test("AI-BEHAVIOR-01 saves and reloads agent execution and chat instructions", async ({
	page,
	api,
}) => {
	const endpoint = "/api/admin/ai/behavior";
	const originalResponse = await api.get(endpoint);
	expect(originalResponse.ok()).toBe(true);
	const original =
		(await originalResponse.json()) as components["schemas"]["AIBehaviorResponse"];
	const nextMode =
		original.agent_execution_mode === "automatic"
			? "delegated"
			: "automatic";
	const instructions = `Acceptance instructions ${randomUUID()}: explain results clearly.`;
	try {
		await page.goto("/settings/ai-chat");
		const executionToggle = page.getByRole("switch", {
			name: "Automatic Agent Execution",
		});
		await expect(executionToggle).toBeChecked({
			checked: original.agent_execution_mode === "automatic",
		});
		const modeSaved = page.waitForResponse(
			(response) =>
				response.request().method() === "PUT" &&
				new URL(response.url()).pathname === endpoint,
		);
		await executionToggle.click();
		expect((await modeSaved).ok()).toBe(true);
		const modePersisted = await api.get(endpoint);
		expect(modePersisted.ok()).toBe(true);
		expect(
			(
				(await modePersisted.json()) as components["schemas"]["AIBehaviorResponse"]
			).agent_execution_mode,
		).toBe(nextMode);
		await page.reload();
		await expect(executionToggle).toBeChecked({
			checked: nextMode === "automatic",
		});

		const input = page.getByRole("textbox", {
			name: "Instructions",
			exact: true,
		});
		await expect(input).toBeEnabled();
		await expect(input).toHaveValue(original.default_system_prompt ?? "");
		await input.fill(instructions);
		const saved = page.waitForResponse(
			(response) =>
				response.request().method() === "PUT" &&
				new URL(response.url()).pathname === endpoint,
		);
		await page
			.getByRole("button", { name: "Save instructions", exact: true })
			.click();
		expect((await saved).ok()).toBe(true);
		const persisted = await api.get(endpoint);
		expect(persisted.ok()).toBe(true);
		expect((await persisted.json()).default_system_prompt).toBe(
			instructions,
		);
		await page.reload();
		await expect(input).toHaveValue(instructions);
	} finally {
		const restored = await api.put(endpoint, {
			data: {
				default_system_prompt: original.default_system_prompt,
				agent_execution_mode: original.agent_execution_mode,
			},
		});
		expect(
			restored.ok(),
			`Restore instructions: ${restored.status()}`,
		).toBe(true);
	}
});
