import { expect, test } from "@playwright/test";

test.describe("Scheduler diagnostics (platform admin)", () => {
	test("shows capacity guidance and opens per-run logs", async ({ page }) => {
		await page.goto("/diagnostics");
		await expect(
			page.getByRole("heading", { name: "Diagnostics" }),
		).toBeVisible();

		await page.getByRole("tab", { name: "Scheduler" }).click();
		const diagnostics = page.getByTestId("scheduler-diagnostics");
		await expect(diagnostics).toBeVisible({ timeout: 15_000 });
		await expect(
			diagnostics.getByText(/Leader healthy|No leader/),
		).toBeVisible();
		await expect(diagnostics.getByText("Scheduler Replicas")).toBeVisible();
		const platformJobs = diagnostics.getByRole("region", {
			name: "Platform Jobs",
		});
		await expect(platformJobs).toBeVisible();
		await expect(
			platformJobs.getByRole("textbox", { name: "Search Platform Jobs" }),
		).toBeVisible();
		await expect(
			platformJobs.getByRole("combobox", {
				name: "Filter Platform Jobs by state",
			}),
		).toBeVisible();
		await expect(
			diagnostics.getByRole("button", {
				name: "Refresh scheduler diagnostics",
			}),
		).toBeVisible();
		await expect(platformJobs.getByRole("columnheader")).toHaveText([
			"Name",
			"State",
			"Elapsed",
			"Memory",
		]);
		await expect(
			diagnostics.getByRole("tab", { name: "System Schedules" }),
		).toBeVisible();
		await diagnostics
			.getByRole("tab", { name: "System Schedules" })
			.click();
		await expect(
			diagnostics
				.getByRole("tabpanel", { name: "System Schedules" })
				.getByRole("columnheader"),
		).toHaveText([
			"Name",
			"State",
			"Schedule",
			"Next Run",
			"Last Run",
			"Memory",
		]);
		await expect(
			diagnostics.getByText("Refresh Expiring OAuth Tokens", {
				exact: true,
			}),
		).toBeVisible();
		await expect(
			diagnostics.getByText(
				/Capacity looks healthy|Capacity action recommended/,
			),
		).toBeVisible();

		await diagnostics
			.getByRole("row", {
				name: "View recent runs for Refresh Expiring OAuth Tokens",
			})
			.click();
		await expect(page.getByRole("dialog")).toContainText("Recent runs");
		await expect(page.getByRole("dialog")).toContainText("Published logs");
		await expect(
			page.getByRole("button", { name: "Copy run ID" }),
		).toBeVisible();
		await page.getByRole("button", { name: "Close" }).click();
		await diagnostics.getByRole("tab", { name: "Platform Jobs" }).click();

		const platformJobRow = platformJobs
			.getByRole("row", { name: /View .* platform job/ })
			.first();
		await expect(platformJobRow).toBeVisible();
		await platformJobRow.click();
		await expect(page.getByRole("dialog")).toContainText(
			/Durable status|Platform job/,
		);
		await expect(
			page.getByRole("button", { name: "Copy job ID" }),
		).toBeVisible();
	});
});
