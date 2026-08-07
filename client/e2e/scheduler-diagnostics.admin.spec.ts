import { expect, test } from "@playwright/test";

test.describe("Scheduler diagnostics (platform admin)", () => {
	test("shows capacity guidance and opens per-run logs", async ({ page }) => {
		await page.goto("/diagnostics");
		await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible();

		await page.getByRole("tab", { name: "Scheduler" }).click();
		const diagnostics = page.getByTestId("scheduler-diagnostics");
		await expect(diagnostics).toBeVisible({ timeout: 15_000 });
		await expect(diagnostics.getByText(/Leader healthy|No leader/)).toBeVisible();
		await expect(diagnostics.getByText("Scheduler replicas")).toBeVisible();
		await expect(diagnostics.getByText("System schedules")).toBeVisible();
		await expect(
			diagnostics.getByText("Refresh Expiring OAuth Tokens"),
		).toBeVisible();
		await expect(
			diagnostics.getByText(/Capacity looks healthy|Capacity action recommended/),
		).toBeVisible();

		await diagnostics
			.getByRole("row", { name: "View recent runs for Refresh Expiring OAuth Tokens" })
			.click();
		await expect(page.getByRole("dialog")).toContainText("Recent runs");
		await expect(page.getByRole("dialog")).toContainText("Published logs");
		await expect(page.getByRole("button", { name: "Copy run ID" })).toBeVisible();
	});
});
