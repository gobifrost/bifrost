import { expect, test } from "@playwright/test";

test("previews the audit archive run from Maintenance", async ({ page }) => {
	await page.goto("/settings/maintenance");

	const card = page
		.locator('[data-slot="card"]')
		.filter({ has: page.getByText("Audit Retention", { exact: true }) });
	await expect(card).toBeVisible();
	await expect(card.getByLabel("Keep in database (days)")).toHaveValue("90");
	await expect(card.getByLabel("Keep in archive (days)")).toHaveValue("365");

	await card.getByRole("button", { name: "Preview" }).click();

	await expect(
		card.getByText(
			/^(Would archive \d[\d,]* events?|Nothing to archive or delete\.)/,
		),
	).toBeVisible();
});
