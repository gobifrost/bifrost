import { expect, test } from "@playwright/test";

const email = process.env.BIFROST_PREVIEW_EMAIL;
const password = process.env.BIFROST_PREVIEW_PASSWORD;
if (!email || !password || !process.env.BIFROST_PREVIEW_URL) {
	throw new Error(
		"BIFROST_PREVIEW_URL, BIFROST_PREVIEW_EMAIL, and BIFROST_PREVIEW_PASSWORD are required for the development preview.",
	);
}

test("admin sees new updates automatically and reopens history from Help", async ({
	page,
}, testInfo) => {
	await page.goto("/login");
	await page.getByLabel("Email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();
	await page.waitForURL((url) => !url.pathname.startsWith("/login"));
	const dialog = page.getByRole("dialog", { name: "What's New" });
	await expect(dialog).toBeVisible();
	await expect(
		dialog.getByRole("heading", {
			name: "Product Updates, Now in Bifrost",
		}),
	).toBeVisible();
	const community = dialog.getByRole("navigation", {
		name: "Bifrost community links",
	});
	await expect(
		community.getByRole("link", { name: "Discord", exact: true }),
	).toHaveAttribute("href", "https://discord.gg/f7TCcWX2s");
	await expect
		.poll(() =>
			page.evaluate(() =>
				Object.keys(localStorage)
					.filter((key) =>
						key.startsWith("bifrost.product-updates.receipts:"),
					)
					.map(
						(key) =>
							JSON.parse(localStorage.getItem(key) ?? "[]")
								.length,
					)
					.reduce((a, b) => a + b, 0),
			),
		)
		.toBe(17);
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-modal-desktop.png"),
	});
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(community).toBeVisible();
	await expect
		.poll(async () => {
			const box = await dialog.boundingBox();
			return box !== null && box.y >= 0 && box.y + box.height <= 844;
		})
		.toBe(true);
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-modal-mobile.png"),
	});
	await dialog.getByRole("link", { name: "View All Updates" }).click();
	await expect(page).toHaveURL(/\/whats-new$/);
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await expect(
		page.getByRole("heading", { name: "Product Updates, Now in Bifrost" }),
	).toBeVisible();
	await expect(page.getByRole("tab")).toHaveCount(0);
	await expect(page.getByRole("button", { name: /Mark.*Read/ })).toHaveCount(
		0,
	);
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-mobile.png"),
	});
	await page.getByRole("button", { name: "Help", exact: true }).click();
	await expect(
		page.getByRole("menuitem", { name: "Release Notes", exact: true }),
	).toBeVisible();
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-help-mobile.png"),
	});
	await page.keyboard.press("Escape");
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.getByRole("button", { name: "Help", exact: true }).click();
	await expect(
		page.getByRole("menuitem", { name: "Documentation", exact: true }),
	).toBeVisible();
	await expect(
		page.getByRole("menuitem", { name: /Copy version/ }),
	).toBeVisible();
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-help-desktop.png"),
	});
	await page
		.getByRole("menuitem", { name: "Release Notes", exact: true })
		.click();
	await expect(
		page.getByRole("heading", { name: "What's New", exact: true }),
	).toBeVisible();
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-desktop.png"),
	});
	await page.reload();
	await expect(
		page.getByRole("heading", { name: "What's New", exact: true }),
	).toBeVisible();
	await expect(page.getByRole("dialog")).toHaveCount(0);
});
