import { expect, test } from "@playwright/test";

test.describe("Scheduler diagnostics (platform admin)", () => {
	test("shows capacity guidance, replicas, schedules, and published logs", async ({ page }) => {
		await page.goto("/diagnostics");
		await expect(page.getByRole("heading", { name: "Diagnostics" })).toBeVisible();

		await page.getByRole("tab", { name: "Scheduler" }).click();
		const diagnostics = page.getByTestId("scheduler-diagnostics");
		await expect(diagnostics).toBeVisible({ timeout: 15_000 });
		await expect(diagnostics.getByText(/Leader healthy|No leader/)).toBeVisible();
		await expect(diagnostics.getByText("Scheduler replicas")).toBeVisible();
		await expect(diagnostics.getByText("System schedules")).toBeVisible();
		await expect(
			diagnostics.getByText("Refresh expiring OAuth tokens"),
		).toBeVisible();
		await expect(diagnostics.getByText("Published system logs")).toBeVisible();
		await expect(
			diagnostics.getByText(/Capacity looks healthy|Capacity action recommended/),
		).toBeVisible();
	});
});
