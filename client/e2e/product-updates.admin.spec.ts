import { expect, test } from "./fixtures/api-fixture";
import type { components } from "../src/lib/v1";

test("What's New scrolls as a page from beside the feed", async ({ page }) => {
	const feed: components["schemas"]["ProductUpdatesFeedResponse"] = {
		bundle: {
			schema_version: 1,
			target_ref: "test",
			content_ref: "test",
			entries: [
				{
					id: "11111111-1111-4111-8111-111111111111",
					revision: 1,
					published_at: "2026-10-01T12:00:00Z",
					title: "Upgrade Overview",
					markdown: "Start with these upgrade steps.",
					area: "Platform",
					type: "Improved",
					action_required: true,
					pinned: true,
					sources: [],
					contributors: [],
					assets: [],
				},
				{
					id: "22222222-2222-4222-8222-222222222222",
					revision: 1,
					published_at: "2026-10-07T12:00:00Z",
					title: "Newer Detail",
					markdown: "Detailed release information.",
					area: "Platform",
					type: "New",
					action_required: false,
					pinned: false,
					sources: [],
					contributors: [],
					assets: [],
				},
				{
					id: "33333333-3333-4333-8333-333333333333",
					revision: 1,
					published_at: "2026-10-06T12:00:00Z",
					title: "Security Detail",
					markdown: "Security detail.",
					area: "Platform",
					type: "Security",
					action_required: false,
					pinned: false,
					sources: [],
					contributors: [],
					assets: [],
				},
				{
					id: "44444444-4444-4444-8444-444444444444",
					revision: 1,
					published_at: "2026-10-08T12:00:00Z",
					title: "Hidden Pinned Detail",
					markdown: "Release-only information.",
					area: "Platform",
					type: "Improved",
					action_required: false,
					pinned: true,
					in_app: false,
					sources: [],
					contributors: [],
					assets: [],
				},
			],
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
	await page.route("**/api/product-updates/receipts", async (route) => {
		const { entry_ids } = route.request().postDataJSON() as {
			entry_ids: string[];
		};
		await route.fulfill({ json: { seen_entry_ids: entry_ids } });
	});
	await page.setViewportSize({ width: 1600, height: 900 });
	await page.goto("/whats-new");
	await expect(page.getByRole("heading", { name: "What's New" })).toBeVisible();
	await expect(page.getByText("Product repair 80", { exact: true })).toBeAttached();
	await expect(page.getByRole("dialog")).toHaveCount(0);
	await expect(page.getByRole("link", { name: "Done" })).toHaveCount(0);
	await expect(page.locator("main h3")).toHaveText([
		"Pinned Updates",
		"New Features and Functionality",
		"Bug Fixes",
		"Hardening",
	]);
	await expect(page.locator("main h4")).toHaveText([
		"Upgrade Overview",
		"Newer Detail",
	]);
	await expect(page.getByText("Upgrade Overview", { exact: true })).toHaveCount(1);
	await expect(page.getByText("Hidden Pinned Detail")).toHaveCount(0);
	await expect(page.getByText("Seen State Wasn't Saved")).toHaveCount(0);

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
