import { expect, test } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

test("What's New scrolls as a page from beside the feed", async ({ page }) => {
	const feed: components["schemas"]["ProductUpdatesFeedResponse"] = {
		bundle: {
			schema_version: 1,
			target_ref: "test",
			content_ref: "test",
			entries: [],
			other_changes: Array.from({ length: 80 }, (_, index) => ({
				source: `pr:${index + 1}`,
				title: `Product repair ${index + 1}`,
				url: `https://github.com/gobifrost/bifrost/pull/${index + 1}`,
				contributors: [],
				category: "fix",
			})),
		},
		seen_entry_ids: [],
	};
	await page.route("**/api/product-updates", (route) =>
		route.fulfill({ json: feed }),
	);
	await page.setViewportSize({ width: 1600, height: 900 });
	await page.goto("/whats-new");
	await expect(page.getByRole("heading", { name: "What's New" })).toBeVisible();
	await expect(page.getByText("Product repair 80", { exact: true })).toBeAttached();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await expect(page.getByRole("link", { name: "Done" })).toHaveCount(0);

	const main = page.getByRole("main");
	const bounds = await main.boundingBox();
	expect(bounds).not.toBeNull();
	if (!bounds) throw new Error("Main content has no layout bounds");
	// This point is in the wide page gutter, outside the centered feed column.
	await page.mouse.move(bounds.x + 40, bounds.y + 200);
	await page.mouse.wheel(0, 600);
	await expect
		.poll(() => main.evaluate((element) => element.scrollTop))
		.toBeGreaterThan(0);
	await expect(
		page.getByRole("heading", { name: "What's New" }),
	).not.toBeInViewport();

	await main.evaluate((element) => {
		element.scrollTop = element.scrollHeight;
	});
	await expect(
		page.getByRole("navigation", { name: "Bifrost community links" }),
	).toBeInViewport();
});
