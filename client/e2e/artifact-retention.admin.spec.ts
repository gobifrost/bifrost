import { expect, test } from "@playwright/test";

test.describe("Artifact retention settings", () => {
	test("configures retention and queues cleanup", async ({ page }, testInfo) => {
		let settings = { enabled: false, retention_days: 90 };
		await page.route(
			"**/api/maintenance/artifact-retention/settings",
			async (route) => {
				if (route.request().method() === "PUT") {
					settings = route.request().postDataJSON() as typeof settings;
				}
				await route.fulfill({ json: settings });
			},
		);
		await page.route(
			"**/api/maintenance/artifact-retention/cleanup",
			async (route) => {
				await route.fulfill({
					status: 202,
					json: {
						job_id: "cleanup-job",
						notification_id: "cleanup-notification",
						status: "queued",
						reused: false,
					},
				});
			},
		);

		await page.goto("/settings/maintenance");
		await expect(page.getByText("Artifact Retention", { exact: true })).toBeVisible();
		await expect(page.getByLabel("Retention Days")).toHaveValue("90");
		await page.getByRole("switch", { name: "Enable Scheduled Cleanup" }).click();
		await expect.poll(() => settings.enabled).toBe(true);
		await page.getByRole("button", { name: "Run Cleanup" }).click();
		await expect(page.getByText("Artifact cleanup queued")).toBeVisible();

		await page.setViewportSize({ width: 1440, height: 1000 });
		await page
			.getByText("Artifact Retention", { exact: true })
			.scrollIntoViewIfNeeded();
		const screenshot = await page.screenshot({
			path: "playwright-results/screenshots/artifact-retention.png",
			fullPage: true,
		});
		await testInfo.attach("Maintenance — Artifact retention", {
			body: screenshot,
			contentType: "image/png",
		});
	});
});
