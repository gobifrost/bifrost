import { expect, test } from "@playwright/test";

test("previews the audit archive run from Maintenance", async ({ page }) => {
	await page.goto("/settings/maintenance");

	await expect(
		page.getByText("Audit Retention", { exact: true }),
	).toBeVisible();
	await expect(page.getByLabel("Keep in database (days)")).toHaveValue("90");
	await expect(page.getByLabel("Keep in archive (days)")).toHaveValue("365");

	await page.getByRole("button", { name: "Preview" }).click();

	await expect(
		page.getByText(
			/^(Would archive \d[\d,]* events?|Nothing to archive or delete\.)/,
		),
	).toBeVisible();
});
