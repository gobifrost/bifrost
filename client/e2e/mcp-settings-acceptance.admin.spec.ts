/**
 * MCP Settings Acceptance (Admin)
 *
 * Exercises the real platform settings UI against persisted Bifrost MCP
 * configuration. The test restores the previous configuration in finally so it
 * can run against a shared admin stack without leaving the MCP defaults changed.
 */

import { expect, test } from "./fixtures/api-fixture";
import type { AuthedApi } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

type MCPConfig = components["schemas"]["MCPConfigResponse"];
type MCPTool = components["schemas"]["MCPToolInfo"];

async function readMcpConfig(api: AuthedApi): Promise<MCPConfig> {
	const response = await api.get("/api/mcp/config");
	expect(
		response.ok(),
		`read MCP config: ${response.status()} ${await response.text()}`,
	).toBe(true);
	return (await response.json()) as MCPConfig;
}

async function readMcpTools(api: AuthedApi): Promise<MCPTool[]> {
	const response = await api.get("/api/mcp/tools");
	expect(
		response.ok(),
		`read MCP tools: ${response.status()} ${await response.text()}`,
	).toBe(true);
	const body =
		(await response.json()) as components["schemas"]["MCPToolsResponse"];
	return body.tools;
}

async function restoreMcpConfig(api: AuthedApi, config: MCPConfig) {
	const response = config.is_configured
		? await api.put("/api/mcp/config", {
				data: {
					enabled: config.enabled,
					allowed_tool_ids: config.allowed_tool_ids ?? null,
					blocked_tool_ids: config.blocked_tool_ids ?? [],
				},
			})
		: await api.delete("/api/mcp/config");

	expect(
		[200, 204],
		`restore MCP config: ${response.status()} ${await response.text()}`,
	).toContain(response.status());
}

function escapeRegExp(value: string) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

async function chooseTool(
	page: import("@playwright/test").Page,
	pickerName: string,
	toolId: string,
) {
	await page.getByRole("combobox", { name: pickerName }).click();
	await page
		.getByRole("option", {
			name: new RegExp(`^${escapeRegExp(toolId)}(?:\\s|$)`),
		})
		.click();
	await page.keyboard.press("Escape");
	await expect(page.getByRole("dialog")).toBeHidden();
}

async function removeSelectedTool(
	page: import("@playwright/test").Page,
	toolId: string,
	sectionTitle: "Allowed Tools" | "Blocked Tools",
) {
	const removeButton = page.getByRole("button", {
		name: `Remove ${toolId} from ${sectionTitle}`,
	});
	if (await removeButton.isVisible()) {
		await removeButton.click();
	}
}

test.describe("MCP settings", () => {
	let agentId = "";
	test.beforeAll(async ({ api }) => {
		const response = await api.post("/api/agents", {
			data: {
				name: `MCP settings fixture ${Date.now()}`,
				system_prompt: "Synthetic settings acceptance fixture.",
				channels: ["chat"],
				access_level: "authenticated",
				system_tools: ["list_workflows", "execute_workflow"],
			},
		});
		expect(
			response.ok(),
			`Create MCP fixture agent: ${response.status()}`,
		).toBe(true);
		agentId = (await response.json()).id;
	});
	test.afterAll(async ({ api }) => {
		if (!agentId) return;
		const response = await api.delete(`/api/agents/${agentId}`);
		expect([200, 204, 404]).toContain(response.status());
	});
	test("admin saves, reloads, resets, and restores MCP configuration", async ({
		page,
		api,
	}) => {
		const previousConfig = await readMcpConfig(api);
		const tools = await readMcpTools(api);
		expect(
			tools.length,
			"MCP settings acceptance needs at least one real tool to select",
		).toBeGreaterThan(0);

		const allowedTool = tools[0];
		const blockedTool = tools.find((tool) => tool.id !== allowedTool.id);
		const nextEnabled = !previousConfig.enabled;
		const targetAllowedToolIds = [allowedTool.id];
		const targetBlockedToolIds = blockedTool ? [blockedTool.id] : [];

		try {
			await page.goto("/settings/mcp");
			await expect(
				page.getByText("External MCP Access", { exact: true }),
			).toBeVisible({ timeout: 15000 });
			await expect(
				page.getByRole("region", { name: "MCP connection details" }),
			).toContainText(/MCP Server URL/);

			const enableSwitch = page.getByRole("switch", {
				name: "Enable MCP Access",
			});
			const allowAllToolsSwitch = page.getByRole("switch", {
				name: "Allow All Tools",
			});
			await expect(enableSwitch).toBeEnabled();
			if ((await enableSwitch.isChecked()) !== nextEnabled) {
				await enableSwitch.click();
			}

			// Make widening and narrowing access explicit before choosing a subset.
			if (await allowAllToolsSwitch.isChecked()) {
				await allowAllToolsSwitch.click();
			}
			await allowAllToolsSwitch.click();
			await expect(allowAllToolsSwitch).toBeChecked();
			await allowAllToolsSwitch.click();
			await expect(allowAllToolsSwitch).not.toBeChecked();
			await chooseTool(page, "Select Allowed Tools", allowedTool.id);
			await expect(
				page.getByText(allowedTool.name, { exact: true }),
			).toBeVisible();

			for (const toolId of previousConfig.blocked_tool_ids ?? []) {
				if (!targetBlockedToolIds.includes(toolId)) {
					await removeSelectedTool(page, toolId, "Blocked Tools");
				}
			}
			if (
				blockedTool &&
				!(previousConfig.blocked_tool_ids ?? []).includes(
					blockedTool.id,
				)
			) {
				await chooseTool(page, "Select Blocked Tools", blockedTool.id);
			}
			if (blockedTool) {
				await expect(
					page.getByText(blockedTool.name, { exact: true }),
				).toBeVisible();
			}

			await page
				.getByRole("button", { name: "Save Configuration" })
				.click();
			await expect(
				page.getByRole("button", { name: "Save Configuration" }),
			).toBeDisabled();

			await page.reload();
			await expect(
				page.getByText("External MCP Access", { exact: true }),
			).toBeVisible({ timeout: 15000 });
			await expect(enableSwitch).toBeChecked({ checked: nextEnabled });
			await expect(allowAllToolsSwitch).not.toBeChecked();
			await expect(
				page.getByText(allowedTool.name, { exact: true }),
			).toBeVisible();
			if (blockedTool) {
				await expect(
					page.getByText(blockedTool.name, { exact: true }),
				).toBeVisible();
			}

			const persisted = await readMcpConfig(api);
			expect(persisted).toMatchObject({
				enabled: nextEnabled,
				allowed_tool_ids: targetAllowedToolIds,
				blocked_tool_ids: targetBlockedToolIds,
				is_configured: true,
			});

			await removeSelectedTool(page, allowedTool.id, "Allowed Tools");
			await page
				.getByRole("button", { name: "Save Configuration" })
				.click();
			await expect(
				page.getByRole("button", { name: "Save Configuration" }),
			).toBeDisabled();
			await expect(allowAllToolsSwitch).not.toBeChecked();
			await expect(
				page.getByText(allowedTool.name, { exact: true }),
			).toBeHidden();

			const emptyAllowedConfig = await readMcpConfig(api);
			expect(emptyAllowedConfig).toMatchObject({
				allowed_tool_ids: [],
				blocked_tool_ids: targetBlockedToolIds,
			});

			await page
				.getByRole("button", { name: "Reset to Defaults" })
				.click();
			const resetDialog = page.getByRole("dialog", {
				name: "Reset MCP configuration?",
			});
			await expect(resetDialog).toBeVisible();
			await resetDialog
				.getByRole("button", { name: "Reset configuration" })
				.click();
			await expect(resetDialog).toBeHidden();

			await page.reload();
			await expect(
				page.getByText("External MCP Access", { exact: true }),
			).toBeVisible({ timeout: 15000 });
			await expect(enableSwitch).toBeChecked();
			await expect(
				page.getByText(allowedTool.name, { exact: true }),
			).toBeHidden();
			if (blockedTool) {
				await expect(
					page.getByText(blockedTool.name, { exact: true }),
				).toBeHidden();
			}

			const resetConfig = await readMcpConfig(api);
			expect(resetConfig).toMatchObject({
				enabled: true,
				allowed_tool_ids: null,
				blocked_tool_ids: [],
				is_configured: false,
			});
		} finally {
			await restoreMcpConfig(api, previousConfig);
		}
	});
});
