import type { Page } from "@playwright/test";

export async function openHeaderHelp(page: Page) {
	await page
		.getByRole("button", { name: "Account menu", exact: true })
		.click();
	await page.getByRole("menuitem", { name: "Help", exact: true }).click();
}

export async function toggleHeaderAppearance(
	page: Page,
	name: "Dark Theme" | "Presentation Mode",
) {
	await page
		.getByRole("button", { name: "Account menu", exact: true })
		.click();
	await page
		.getByRole("menuitem", { name: "Appearance", exact: true })
		.click();
	await page.getByRole("menuitemcheckbox", { name, exact: true }).click();
}
