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

test("filters policy denials by file path and opens one in the detail drawer", async ({
	page,
}) => {
	const path = `browser-denial-${Date.now()}.txt`;
	await page.goto("/audit");
	await expect(
		page.getByRole("heading", { name: "Audit Log" }),
	).toBeVisible();
	expect(await createDeniedFileRead(page, path)).toBe(403);

	await page.getByRole("combobox", { name: "Action filter" }).click();
	await page.getByRole("option", { name: "Policy denials" }).click();
	await page.getByRole("combobox", { name: "Outcome filter" }).click();
	await page.getByRole("option", { name: "Failure" }).click();
	await page
		.getByRole("searchbox", { name: "Search audit events" })
		.fill(path);

	await expect(page.getByText(`audit-browser / ${path}`)).toBeVisible();
	const opener = page.getByRole("button", {
		name: "policy.deny",
		exact: true,
	});
	await expect(opener).toBeVisible();

	await opener.click();
	const drawer = page.getByRole("dialog", { name: "Audit Event" });
	await expect(drawer.getByText(path, { exact: true })).toBeVisible();
	await page.keyboard.press("Escape");
	await expect(drawer).toBeHidden();
	await expect(opener).toBeFocused();
});

test("Access Checks shows its empty state when no check would be denied", async ({
	page,
}) => {
	await page.goto("/audit/access-checks");
	await expect(page.getByText("No Would-Deny Checks")).toBeVisible();
});
