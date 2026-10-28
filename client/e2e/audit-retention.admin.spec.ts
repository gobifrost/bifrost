import { expect, test } from "@playwright/test";

test("previews the audit archive run from Maintenance", async ({ page }) => {
	await page.goto("/settings/maintenance");

	const card = page
		.locator('[data-slot="card"]')
		.filter({ has: page.getByText("Audit Retention", { exact: true }) });
	await expect(card).toBeVisible();
	// The fields stay disabled until the saved policy loads; read it after that.
	const databaseDays = card.getByLabel("Keep in database (days)");
	await expect(databaseDays).toBeEnabled();
	await expect(databaseDays).toHaveValue("30");
	await expect(card.getByLabel("Keep in archive (days)")).toHaveValue("365");

	await card.getByRole("button", { name: "Preview" }).click();

	// Preview is a PlatformJob: claim plus child-process start puts its floor at 4-6s.
	await expect(
		card.getByText(
			/^(Would archive \d[\d,]* events?|Nothing to archive or delete\.)/,
		),
	).toBeVisible({ timeout: 30_000 });
});
