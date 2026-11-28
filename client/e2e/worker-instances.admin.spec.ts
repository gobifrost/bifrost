import { expect, test } from "./fixtures/api-fixture";

test("shows canonical worker instances in Diagnostics and filters by type", async ({
	page,
}) => {
	await page.goto("/diagnostics");

	const inventory = page.getByRole("region", { name: "Worker Instances" });
	const ready = inventory.getByRole("row", { name: /Static.*Ready/ }).first();
	await expect(ready).toBeVisible();

	await inventory.getByRole("combobox", { name: "Worker Type" }).click();
	await page.getByRole("option", { name: "External" }).click();
	await expect(
		inventory.getByText("No worker instances found."),
	).toBeVisible();
});
