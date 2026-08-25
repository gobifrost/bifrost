import { expect, test, type Page } from "@playwright/test";

async function createDeniedFileRead(page: Page, path: string) {
	return page.evaluate(async (deniedPath) => {
		const token = localStorage.getItem("bifrost_access_token");
		const response = await fetch("/api/files/read", {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				location: "audit-browser",
				scope: "global",
				path: deniedPath,
				mode: "cloud",
			}),
		});
		return response.status;
	}, path);
}

test("filters policy denials by file path", async ({ page }) => {
	const path = `browser-denial-${Date.now()}.txt`;
	await page.goto("/audit");
	await expect(page.getByRole("heading", { name: "Audit Log" })).toBeVisible();
	expect(await createDeniedFileRead(page, path)).toBe(403);

	await page.getByRole("combobox", { name: "Action filter" }).click();
	await page.getByRole("option", { name: "Policy denials" }).click();
	await page.getByRole("combobox", { name: "Outcome filter" }).click();
	await page.getByRole("option", { name: "Failure" }).click();
	await page
		.getByRole("searchbox", { name: "Search audit events" })
		.fill(path);

	await expect(page.getByText(`audit-browser / ${path}`)).toBeVisible();
	await expect(page.getByText("policy.deny", { exact: true })).toBeVisible();
});
