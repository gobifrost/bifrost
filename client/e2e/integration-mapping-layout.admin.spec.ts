/**
 * Integration Mapping Layout (Admin)
 *
 * Locks in the mappings-tab scroll model: the tab's sticky toolbar must stay
 * reachable while the mapping list scrolls with the page, the list itself must
 * not own a second vertical scrollbar, and the row layout must not overflow
 * horizontally at tablet widths.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Locator, Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10_000)}`;
const INTEGRATION_NAME = `E2E Layout ${UNIQUE}`;
const ORG_COUNT = 24;

type Organization = { id: string };

function mappingRow(page: Page, orgName: string): Locator {
	return page
		.getByRole("list", { name: "Organization mappings" })
		.getByRole("listitem")
		.filter({ hasText: orgName });
}

async function openMappings(page: Page, integrationId: string) {
	await page.goto(`/integrations/${integrationId}`);
	await page.getByRole("tab", { name: "Mappings" }).click();
	await expect(
		page.getByRole("list", { name: "Organization mappings" }),
	).toBeVisible({ timeout: 10_000 });
}

test.describe("Integration mapping layout", () => {
	let integrationId = "";
	const orgIds: string[] = [];

	test.beforeAll(async ({ api }) => {
		for (let index = 0; index < ORG_COUNT; index += 1) {
			const created = await api.post("/api/organizations", {
				data: {
					name: `Layout Org ${String(index).padStart(2, "0")} ${UNIQUE}`,
					domain: `layout-${index}-${UNIQUE}.gobifrost.dev`,
				},
			});
			expect(created.ok(), await created.text()).toBe(true);
			orgIds.push(((await created.json()) as Organization).id);
		}

		const integration = await api.post("/api/integrations", {
			data: { name: INTEGRATION_NAME },
		});
		expect(integration.ok(), await integration.text()).toBe(true);
		integrationId = ((await integration.json()) as { id: string }).id;

		// Map half the organizations so the list mixes mapped/unmapped rows.
		for (const [index, orgId] of orgIds.slice(0, ORG_COUNT / 2).entries()) {
			const mapping = await api.post(
				`/api/integrations/${integrationId}/mappings`,
				{
					data: {
						organization_id: orgId,
						entity_id: `layout-entity-${index}`,
						entity_name: `Layout Entity ${index}`,
					},
				},
			);
			expect(mapping.ok(), await mapping.text()).toBe(true);
		}
	});

	test.afterAll(async ({ api }) => {
		if (integrationId) {
			expect([200, 204, 404]).toContain(
				(
					await api.delete(`/api/integrations/${integrationId}`)
				).status(),
			);
		}
		for (const orgId of orgIds) {
			expect([200, 204, 404]).toContain(
				(await api.delete(`/api/organizations/${orgId}`)).status(),
			);
		}
	});

	test("[MAPPING-LAYOUT-01] desktop scrolls the list with a pinned toolbar and no nested scroller", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1440, height: 800 });
		await openMappings(page, integrationId);

		const list = page.getByRole("list", { name: "Organization mappings" });
		await expect(
			mappingRow(page, `Layout Org ${ORG_COUNT - 1} ${UNIQUE}`),
		).toBeAttached();

		const geometry = await page.evaluate(() => {
			const list = document.querySelector(
				'[aria-label="Organization mappings"]',
			)!;
			const pageScroll = document.querySelector("[data-page-scroll]")!;
			const main = document.querySelector("main")!;
			const scrollOwners: string[] = [];
			for (const el of [main, pageScroll]) {
				const style = getComputedStyle(el);
				if (
					style.overflowY === "auto" &&
					el.scrollHeight > el.clientHeight + 1
				) {
					scrollOwners.push(
						el.hasAttribute("data-page-scroll")
							? "page-scroll"
							: "main",
					);
				}
			}
			const scrolling = document.scrollingElement!;
			scrolling.scrollTop = 400;
			const documentScrollable = scrolling.scrollTop > 0;
			scrolling.scrollTop = 0;
			return {
				listOwnScroll:
					getComputedStyle(list).overflowY !== "visible" &&
					list.scrollHeight > list.clientHeight + 1,
				listOverflowsPageScroll:
					pageScroll.scrollHeight > pageScroll.clientHeight + 1,
				scrollOwners,
				documentScrollable,
				// A phantom document height means absolutely positioned row labels
				// escaped the scroll region and drew a second page below the shell.
				documentOverflow:
					document.documentElement.scrollHeight - window.innerHeight,
			};
		});

		expect(geometry.listOverflowsPageScroll).toBe(true);
		expect(geometry.listOwnScroll).toBe(false);
		expect(geometry.scrollOwners).toEqual(["page-scroll"]);
		expect(geometry.documentScrollable).toBe(false);
		expect(geometry.documentOverflow).toBeLessThanOrEqual(1);

		// Scroll to the end of the mapping list; the toolbar must stay pinned.
		await page.evaluate(() => {
			const pageScroll = document.querySelector("[data-page-scroll]")!;
			pageScroll.scrollTop = pageScroll.scrollHeight;
		});

		await expect(
			page.getByText("Organization Mappings", { exact: true }),
		).toBeInViewport();
		await expect(
			page.getByRole("searchbox", {
				name: "Search organization mappings",
			}),
		).toBeInViewport();
		await expect(list.getByRole("listitem").last()).toBeInViewport();
	});

	test("[MAPPING-LAYOUT-02] row status aligns with the entity control and actions sit at the trailing edge", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 1440, height: 800 });
		await openMappings(page, integrationId);

		const row = mappingRow(page, `Layout Org 00 ${UNIQUE}`);
		const entity = row.getByLabel("External entity ID");
		const badge = row.getByText("Mapped", { exact: true });
		const actions = row.getByRole("button", {
			name: /mapping actions/i,
		});

		await expect(entity).toBeVisible();
		const [entityBox, badgeBox, actionBox, rowBox] = await Promise.all([
			entity.boundingBox(),
			badge.boundingBox(),
			actions.boundingBox(),
			row.boundingBox(),
		]);
		for (const box of [entityBox, badgeBox, actionBox, rowBox]) {
			expect(box).not.toBeNull();
		}

		const entityCenter = entityBox!.y + entityBox!.height / 2;
		const badgeCenter = badgeBox!.y + badgeBox!.height / 2;
		expect(Math.abs(entityCenter - badgeCenter)).toBeLessThanOrEqual(1);
		expect(
			Math.round(rowBox!.x + rowBox!.width - (actionBox!.x + actionBox!.width)),
		).toBeLessThanOrEqual(1);

		// Row actions (including Configure) live in the trailing menu.
		await actions.click();
		await expect(
			page.getByRole("menuitem", { name: "Configure" }),
		).toBeVisible();
	});

	test("[MAPPING-LAYOUT-03] tablet width stacks rows without horizontal overflow", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 768, height: 1024 });
		await openMappings(page, integrationId);

		const overflow = await page.evaluate(
			() =>
				document.documentElement.scrollWidth -
				document.documentElement.clientWidth,
		);
		expect(overflow).toBeLessThanOrEqual(1);

		const row = mappingRow(page, `Layout Org 00 ${UNIQUE}`);
		const entity = row.getByLabel("External entity ID");
		const actions = row.getByRole("button", {
			name: /mapping actions/i,
		});
		await expect(entity).toBeVisible();

		const [entityBox, actionBox] = await Promise.all([
			entity.boundingBox(),
			actions.boundingBox(),
		]);
		expect(entityBox!.x + entityBox!.width).toBeLessThanOrEqual(768);
		expect(actionBox!.x + actionBox!.width).toBeLessThanOrEqual(768);
	});

	test("[MAPPING-LAYOUT-04] mobile keeps natural page scrolling with a pinned toolbar", async ({
		page,
	}) => {
		await page.setViewportSize({ width: 390, height: 844 });
		await openMappings(page, integrationId);

		const overflow = await page.evaluate(
			() =>
				document.documentElement.scrollWidth -
				document.documentElement.clientWidth,
		);
		expect(overflow).toBeLessThanOrEqual(1);

		const owners = await page.evaluate(() => {
			const found: string[] = [];
			for (const el of document.querySelectorAll("main, [data-page-scroll]")) {
				const style = getComputedStyle(el);
				if (
					style.overflowY === "auto" &&
					el.scrollHeight > el.clientHeight + 1
				) {
					found.push(
						el.hasAttribute("data-page-scroll")
							? "page-scroll"
							: "main",
					);
				}
			}
			return found;
		});
		expect(owners).toEqual(["main"]);
		expect(
			await page.evaluate(
				() =>
					document.documentElement.scrollHeight - window.innerHeight,
			),
		).toBeLessThanOrEqual(1);

		await page.evaluate(() => {
			const main = document.querySelector("main")!;
			main.scrollTop = main.scrollHeight;
		});
		await expect(
			page.getByRole("searchbox", {
				name: "Search organization mappings",
			}),
		).toBeInViewport();
	});
});
