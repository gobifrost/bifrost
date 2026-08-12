import { expect, test } from "@playwright/test";

test.describe("Bifrost Agent", () => {
	test("downloads the instance Agent Plugin from the header", async ({
		page,
	}) => {
		await page.goto("/agents");

		await page
			.getByRole("button", { name: "Connect AI assistants" })
			.click();

		await expect(page.getByText("Use Bifrost with AI")).toBeVisible();
		await expect(page.getByText(/Claude Code, Codex/)).toBeVisible();
		await expect(
			page.getByText(/Claude Desktop, Microsoft Copilot/),
		).toBeVisible();
		await expect(page.getByText("1. Connect the MCP server")).toBeVisible();
		await expect(page.getByText("2. Add the Bifrost behavior")).toBeVisible();

		const downloadPromise = page.waitForEvent("download");
		await page
			.getByRole("button", { name: "Download Agent Plugin" })
			.click();
		const download = await downloadPromise;

		expect(download.suggestedFilename()).toBe("bifrost-agent.zip");
	});
});
