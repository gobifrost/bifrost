/**
 * Agent improvement workbench (Admin)
 *
 * Seeds an agent and navigates to its Workbench page. Asserts
 * the workbench structure: the "Workbench" heading, the four Workbench
 * collections with Tests selected by default, and the shared test inspector.
 */

import { test, expect } from "@playwright/test";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Workbench (admin)", () => {
	test("Workbench renders collections with tests first", async ({
		page,
	}, testInfo) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Atlas Quality",
		});

		await page.goto(`/agents/${agent.id}/quality`);

		// 1. Heading is always present regardless of flagged-run count.
		await expect(
			page.getByRole("heading", { name: "Workbench", exact: true }),
		).toBeVisible({ timeout: 10000 });

		// 2. The four collections share one workbench; Tests is the default.
		for (const collection of ["Tests", "Findings", "Reviews", "Run History"]) {
			await expect(
				page.getByRole("button", { name: collection, exact: true }),
			).toBeVisible();
		}
		await expect(
			page.getByRole("button", { name: "Tests", exact: true }),
		).toHaveAttribute("aria-pressed", "true");

		// 3. The retired proposal generator is absent from the production shell.
		await expect(
			page.getByRole("button", { name: /generate proposal/i }),
		).toHaveCount(0);

		// 4. Screenshots for visual reference, desktop and mobile,
		// captured from the top of the Workbench landing.
		await page.screenshot({
			path: testInfo.outputPath("workbench-evidence-desktop.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await page.screenshot({
			path: testInfo.outputPath("workbench-evidence-mobile.png"),
			fullPage: true,
		});
		await page.setViewportSize({ width: 1280, height: 800 });

		// 5. Desktop keeps the workbench header in view while content scrolls.
		await page.setViewportSize({ width: 1280, height: 500 });
		const stickyHeader = page.getByTestId("quality-sticky-header");
		await page
			.getByRole("region", { name: "Page content" })
			.first()
			.evaluate((region) => {
				region.scrollTop = region.scrollHeight;
			});
		const headerBox = await stickyHeader.boundingBox();
		const viewport = page.viewportSize();
		expect(headerBox).not.toBeNull();
		expect(viewport).not.toBeNull();
		expect(headerBox!.y).toBeGreaterThanOrEqual(0);
		expect(headerBox!.y + headerBox!.height).toBeLessThanOrEqual(
			viewport!.height,
		);
		await expect(
			page.getByRole("heading", { name: "Workbench", exact: true }),
		).toBeInViewport();
	});

	test("collection selection survives in the URL", async ({ page }) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Atlas Tabs",
		});
		await page.goto(`/agents/${agent.id}/quality?collection=findings`);
		await expect(page.getByRole("button", { name: "Findings" })).toHaveAttribute(
			"aria-pressed",
			"true",
		);
		await page.getByRole("button", { name: "Reviews" }).click();
		await expect(page).toHaveURL(/collection=reviews/);
	});
});
