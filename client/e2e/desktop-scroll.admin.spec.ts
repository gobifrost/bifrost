import { expect, test } from "@playwright/test";

for (const height of [600, 900]) {
	for (const width of [1440, 1100]) {
		for (const filtersOpen of [false, true]) {
			test(`document records accept wheel scrolling at ${width}×${height}px, filters ${filtersOpen}`, async ({
				page,
			}, testInfo) => {
				await page.setViewportSize({ width, height });
				const id = "00000000-0000-4000-8000-000000000001";
				await page.route(`**/api/tables/${id}`, (route) =>
					route.fulfill({
						json: {
							id,
							name: "Scroll documents",
							description: "Document scrolling fixture",
							organization_id: null,
						},
					}),
				);
				await page.route(
					`**/api/tables/${id}/documents/query`,
					(route) =>
						route.fulfill({
							json: {
								documents: Array.from(
									{ length: 40 },
									(_, i) => ({
										id: `scroll-document-${i}`,
										data: { name: `Device ${i}` },
										created_at: null,
									}),
								),
								total: 40,
							},
						}),
				);
				await page.goto(`/tables/${id}`);
				await expect(
					page.getByRole("heading", {
						name: "Scroll documents",
						exact: true,
					}),
				).toBeVisible();
				if (filtersOpen)
					await page
						.getByRole("button", { name: /^Filters/ })
						.click();
				const region = page.getByRole("region", {
					name: "Documents",
					exact: true,
				});
				await expect(
					region
						.getByText("scroll-document-39", { exact: true })
						.first(),
				).toBeAttached();
				const table = region.locator("table:visible");
				const records = (await table.count())
					? table.locator("tbody > tr")
					: region
							.getByRole("list", { name: "Document records" })
							.locator(":scope > li");
				await expect(records).toHaveCount(40);
				const owner = await records
					.first()
					.evaluateHandle((element) => {
						let parent = element.parentElement;
						while (
							parent &&
							!(
								getComputedStyle(parent).overflowY === "auto" &&
								parent.scrollHeight > parent.clientHeight &&
								parent.clientHeight > 0
							)
						)
							parent = parent.parentElement;
						return parent;
					});
				const bounds = await owner.evaluate((element) => {
					if (!(element instanceof HTMLElement))
						throw new Error("Records have no scrolling ancestor");
					const r = element.getBoundingClientRect();
					return {
						x: r.x,
						y: r.y,
						width: r.width,
						height: r.height,
						tag: element.tagName,
					};
				});
				expect(bounds.tag).not.toBe("MAIN");
				expect(bounds.height).toBeGreaterThan(75);
				const lastValue = records
					.last()
					.getByText("Device 39", { exact: true })
					.first();
				await lastValue.scrollIntoViewIfNeeded();
				await expect(lastValue).toBeInViewport();
				await testInfo.attach("Readable document detail", {
					body: await page.screenshot({ animations: "disabled" }),
					contentType: "image/png",
				});
				await page.mouse.move(
					bounds.x + bounds.width / 2,
					bounds.y + bounds.height / 2,
				);
				await page.mouse.wheel(0, 20000);
				await expect(records.last()).toBeInViewport();
				await expect(
					page.getByRole("heading", {
						name: "Scroll documents",
						exact: true,
					}),
				).toBeInViewport();
				await expect(
					page.getByRole("combobox", { name: "Documents per page" }),
				).toBeInViewport();
				await testInfo.attach("Scrolled document workspace", {
					body: await page.screenshot({ animations: "disabled" }),
					contentType: "image/png",
				});
			});
		}
	}
}

const configs = (count: number) =>
	Array.from({ length: count }, (_, index) => ({
		id: `scroll-config-${index}`,
		key: `scroll_key_${index}`,
		value: `Value ${index}`,
		type: "string",
		scope: "global",
		org_id: null,
		description: "Scroll layout fixture",
		integration_name: null,
	}));

for (const height of [900, 600]) {
	test(`desktop table owns overflow at ${height}px without moving page controls`, async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1440, height });
		await page.route(/\/api\/config(?:\?.*)?$/, (route) =>
			route.fulfill({ json: configs(80) }),
		);
		await page.goto("/config");
		const heading = page.getByRole("heading", {
			name: "Configuration",
			exact: true,
		});
		await expect(
			page.getByRole("button", {
				name: "scroll_key_79",
				exact: true,
			}),
		).toBeAttached();
		const table = page.locator("main table").first();
		const before = await heading.boundingBox();
		const geometry = await table.evaluate((element) => {
			const body = element.parentElement!;
			const main = element.closest("main")!;
			return {
				scrolls: body.scrollHeight > body.clientHeight + 1,
				height: body.clientHeight,
				pageOverflow: main.scrollHeight - main.clientHeight,
			};
		});
		expect(geometry.scrolls).toBe(true);
		expect(geometry.height).toBeGreaterThan(100);
		expect(geometry.pageOverflow).toBeLessThanOrEqual(2);
		await table.evaluate((element) => {
			element.parentElement!.scrollTop =
				element.parentElement!.scrollHeight;
		});
		await expect(
			page.getByRole("button", {
				name: "scroll_key_79",
				exact: true,
			}),
		).toBeInViewport();
		await expect(
			page.getByRole("columnheader", { name: "Key", exact: true }),
		).toBeInViewport();
		expect((await heading.boundingBox())!.y).toBe(before!.y);
	});
}

test("short tables use only their content height", async ({ page }) => {
	await page.setViewportSize({ width: 1440, height: 900 });
	await page.route(/\/api\/config(?:\?.*)?$/, (route) =>
		route.fulfill({ json: configs(2) }),
	);
	await page.goto("/config");
	await expect(
		page.getByRole("button", { name: "scroll_key_1", exact: true }),
	).toBeVisible();
	const tableHeight = await page
		.locator("main table")
		.first()
		.evaluate(
			(element) =>
				element.parentElement!.parentElement!.getBoundingClientRect()
					.height,
		);
	const available = await page
		.locator("[data-page-scroll]")
		.evaluate((element) => element.clientHeight);
	expect(tableHeight).toBeLessThan(available - 100);
});

test("mobile records keep natural page scrolling", async ({ page }) => {
	await page.setViewportSize({ width: 390, height: 844 });
	await page.route(/\/api\/config(?:\?.*)?$/, (route) =>
		route.fulfill({ json: configs(30) }),
	);
	await page.goto("/config");
	await expect(
		page.getByRole("button", { name: "scroll_key_29", exact: true }),
	).toBeAttached();
	await expect(page.locator("main table")).toHaveCount(0);
	const region = await page
		.locator("[data-page-scroll]")
		.evaluate((element) => ({
			height: element.clientHeight,
			content: element.scrollHeight,
		}));
	expect(region.content - region.height).toBeLessThanOrEqual(2);
	await page
		.getByRole("button", { name: "scroll_key_29", exact: true })
		.scrollIntoViewIfNeeded();
	await expect(
		page.getByRole("button", { name: "scroll_key_29", exact: true }),
	).toBeInViewport();
	await expect(
		page.getByRole("heading", { name: "Configuration", exact: true }),
	).not.toBeInViewport();
});

for (const width of [1440, 1100]) {
	test(`event source events accept wheel scrolling at ${width}px`, async ({
		page,
	}) => {
		await page.setViewportSize({ width, height: 760 });
		const sourceId = "00000000-0000-4000-8000-0000000000e1";
		const source = {
			id: sourceId,
			name: "Scroll events",
			source_type: "webhook",
			organization_id: null,
			is_active: true,
			subscription_count: 0,
			event_count_24h: 60,
			webhook: {
				adapter_name: "generic",
				callback_url: `/api/hooks/${sourceId}`,
			},
			created_at: "2026-10-15T12:00:00Z",
			updated_at: "2026-10-15T12:00:00Z",
		};
		await page.route(
			new RegExp(`/api/events/sources/${sourceId}$`),
			(route) => route.fulfill({ json: source }),
		);
		await page.route(
			new RegExp(`/api/events/sources/${sourceId}/events(?:\\?.*)?$`),
			(route) =>
				route.fulfill({
					json: {
						items: Array.from({ length: 60 }, (_, i) => ({
							id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
							event_source_id: sourceId,
							event_type: `scroll.event.${i}`,
							received_at: new Date(
								Date.now() - i * 60_000,
							).toISOString(),
							headers: {},
							data: {},
							source_ip: "10.0.0.1",
							status: "completed",
							delivery_count: 0,
							success_count: 0,
							failed_count: 0,
							created_at: "2026-10-15T12:00:00Z",
						})),
						total: 60,
					},
				}),
		);
		await page.goto(`/event-sources/${sourceId}`);
		await expect(
			page.getByRole("heading", { name: "Scroll events", exact: true }),
		).toBeVisible();
		const last = page
			.getByText("scroll.event.59", { exact: true })
			.filter({ visible: true });
		await expect(last).toBeAttached();
		await expect(last).not.toBeInViewport();
		const bounds = await last.evaluate((element) => {
			let parent = element.parentElement;
			while (
				parent &&
				!(
					getComputedStyle(parent).overflowY === "auto" &&
					parent.scrollHeight > parent.clientHeight
				)
			)
				parent = parent.parentElement;
			if (!parent) throw new Error("Events have no scrolling ancestor");
			const r = parent.getBoundingClientRect();
			return { x: r.x, y: r.y, width: r.width, height: r.height };
		});
		await page.mouse.move(
			bounds.x + bounds.width / 2,
			bounds.y + bounds.height / 2,
		);
		await page.mouse.wheel(0, 40000);
		await expect(last).toBeInViewport();
		await expect(
			page.getByRole("button", { name: "Copy Webhook URL" }),
		).toBeInViewport();
	});
}
