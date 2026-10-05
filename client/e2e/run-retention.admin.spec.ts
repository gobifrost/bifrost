import { expect, test } from "@playwright/test";

test("changes the run history window and previews a run from Maintenance", async ({
	page,
}) => {
	await page.goto("/settings/maintenance");

	const card = page
		.locator('[data-slot="card"]')
		.filter({ has: page.getByText("Run history", { exact: true }) });
	await expect(card).toBeVisible();
	const days = card.getByLabel("Keep finished runs and events (days)");
	await expect(days).toHaveValue("30");

	// Lengthening the window saves on blur, with no confirmation.
	await days.fill("45");
	await days.blur();
	await expect(page.getByText("Run history settings saved")).toBeVisible();

	await page.reload();
	await expect(days).toHaveValue("45");

	await card.getByRole("button", { name: "Preview" }).click();
	await expect(
		card.getByRole("status").filter({
			hasText:
				/^(Would delete \d[\d,]* workflow runs?|Nothing to delete\.)/,
		}),
	).toBeVisible();

	// Shortening it back asks first, because deletion is permanent.
	await days.fill("30");
	await days.blur();
	await page.getByRole("button", { name: "Delete and save" }).click();
	await expect(page.getByText("Run history settings saved")).toBeVisible();

	await page.reload();
	await expect(days).toHaveValue("30");
});
