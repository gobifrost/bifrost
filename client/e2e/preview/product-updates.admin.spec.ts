import { expect, test } from "@playwright/test";

const email = process.env.BIFROST_PREVIEW_EMAIL;
const password = process.env.BIFROST_PREVIEW_PASSWORD;

if (!email || !password || !process.env.BIFROST_PREVIEW_URL) {
	throw new Error(
		"BIFROST_PREVIEW_URL, BIFROST_PREVIEW_EMAIL, and BIFROST_PREVIEW_PASSWORD are required for the development preview.",
	);
}

test("platform admin reads and acknowledges a product update", async ({
	page,
}, testInfo) => {
	await page.goto("/login");
	await page.getByLabel("Email").fill(email);
	await page.getByLabel("Password", { exact: true }).fill(password);
	await page.getByRole("button", { name: "Sign In", exact: true }).click();
	await page.waitForURL((url) => !url.pathname.startsWith("/login"));

	await page
		.getByRole("navigation", { name: "Primary navigation" })
		.getByRole("link", { name: /What's New/ })
		.click();
	await expect(page.getByRole("heading", { name: "What's New" })).toBeVisible();
	await expect(
		page.getByRole("heading", { name: "Product Updates, Now in Bifrost" }),
	).toBeVisible();
	const community = page.getByRole("navigation", { name: "Bifrost community links" });
	await expect(community.getByRole("link", { name: "Discord", exact: true })).toHaveAttribute(
		"href",
		"https://discord.gg/f7TCcWX2s",
	);
	await page.screenshot({
		path: testInfo.outputPath("whats-new-desktop.png"),
	});

	await page.getByRole("button", { name: "Mark Presented Updates Read" }).click();
	await expect(page.getByText("Unread (0)")).toBeVisible();
	await expect(page.getByLabel(/unread product updates/)).toHaveCount(0);

	await page.setViewportSize({ width: 390, height: 844 });
	await expect(page.getByRole("navigation", { name: "Bifrost community links" })).toBeVisible();
	await page.screenshot({
		path: testInfo.outputPath("whats-new-mobile.png"),
	});
});
