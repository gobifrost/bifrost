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
			name: "Release Notes and Discord",
		}),
	).toBeVisible();
	await expect(dialog.getByRole("heading", { name: "October 6, 2026", exact: true })).toHaveCount(1);
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
		.toBe(16);
	await page.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-modal-desktop.png"),
	});
	await page.setViewportSize({ width: 390, height: 844 });
	await expect(community).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Done", exact: true })).toBeVisible();
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
	await expect(
		dialog.getByRole("link", { name: "Open Users", exact: true }),
	).toHaveAttribute("target", "_blank");
	const [history] = await Promise.all([
		page.waitForEvent("popup"),
		dialog.getByRole("link", { name: "View All Updates" }).click(),
	]);

	await expect(history).toHaveURL(/\/whats-new$/);
	await history.setViewportSize({ width: 390, height: 844 });
	await expect(history.getByRole("dialog")).toHaveCount(0);
	await expect(
		history.getByRole("heading", { name: "Release Notes and Discord" }),
	).toBeVisible();
	await expect(history.getByRole("heading", { name: "October 6, 2026", exact: true })).toHaveCount(1);
	await expect(history.getByText("Preview Controls", { exact: true })).toHaveCount(0);
	await expect(history.getByRole("tab")).toHaveCount(0);
	await expect(
		history.getByRole("button", { name: /Mark.*Read/ }),
	).toHaveCount(0);
	await expect(history.getByRole("link", { name: "Done", exact: true })).toBeVisible();
	await expect(history.getByText("models.dev", { exact: false })).toHaveCount(0);
	await expect(history.getByRole("heading", { name: "Dependency Security Updates" })).toHaveCount(0);
	await expect(history.getByText("Action Required", { exact: true })).toHaveCount(1);
	await expect(history.getByText("CLI/SDK users:", { exact: true })).toBeVisible();
	await history.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-mobile.png"),
	});
	await expect.poll(() => history.getByRole("img", { name: "Effective access and role assignments", exact: true }).evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
	await history.getByRole("heading", { name: "Set History Retention", exact: true }).scrollIntoViewIfNeeded();
	await expect.poll(() => history.getByRole("img", { name: "Run history retention settings", exact: true }).evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
	await history.getByRole("img", { name: "Run history retention settings", exact: true }).scrollIntoViewIfNeeded();
	await history.screenshot({ animations: "disabled", path: testInfo.outputPath("whats-new-retention-mobile.png") });
	await history.getByRole("heading", { name: "Bug Fixes", exact: true }).scrollIntoViewIfNeeded();
	await expect(history.getByRole("heading", { name: "Bug Fixes", exact: true })).toHaveCount(1);
	await expect(history.getByRole("link", { name: "Done", exact: true })).toBeVisible();
	await history.screenshot({ animations: "disabled", path: testInfo.outputPath("whats-new-fixes-mobile.png") });
	await history.getByRole("heading", { name: "Hardening", exact: true }).scrollIntoViewIfNeeded();
	await history.screenshot({ animations: "disabled", path: testInfo.outputPath("whats-new-hardening-mobile.png") });
	await history.getByRole("heading", { name: "Release Notes and Discord", exact: true }).scrollIntoViewIfNeeded();
	await history.getByRole("button", { name: "Help", exact: true }).click();
	await expect(
		history.getByRole("menuitem", { name: "Release Notes", exact: true }),
	).toBeVisible();
	await history.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-help-mobile.png"),
	});
	await history.keyboard.press("Escape");
	await history.setViewportSize({ width: 1440, height: 1000 });
	await history.getByRole("button", { name: "Help", exact: true }).click();
	await expect(
		history.getByRole("menuitem", { name: "Documentation", exact: true }),
	).toBeVisible();
	await expect(
		history.getByRole("menuitem", { name: /Copy version/ }),
	).toBeVisible();
	await history.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-help-desktop.png"),
	});
	await history
		.getByRole("menuitem", { name: "Release Notes", exact: true })
		.click();
	await expect(
		history.getByRole("heading", { name: "What's New", exact: true }),
	).toBeVisible();
	await history.screenshot({
		animations: "disabled",
		path: testInfo.outputPath("whats-new-desktop.png"),
	});
	await history.reload();
	await expect(
		history.getByRole("heading", { name: "What's New", exact: true }),
	).toBeVisible();
	await expect(history.getByRole("dialog")).toHaveCount(0);
	await history.getByRole("link", { name: "Done", exact: true }).click();
	await expect(history).toHaveURL(/\/$/);
});
