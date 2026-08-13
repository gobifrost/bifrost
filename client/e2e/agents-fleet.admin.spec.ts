/**
 * Agents Fleet Page (Admin)
 *
 * Smoke tests for the new fleet view (`/agents`): heading + stats render,
 * search filters, grid/table view toggle. Captures a screenshot for the
 * Phase 5 visual review pass.
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Agents Fleet Page (admin)", () => {
	test("hides inactive agents until Show Inactive is enabled", async ({
		page,
		api,
	}) => {
		const name = `Inactive Fleet Agent ${Date.now()}`;
		const createResponse = await api.post("/api/agents", {
			data: {
				name,
				system_prompt:
					"You are an inactive fleet visibility test agent.",
				access_level: "authenticated",
			},
		});
		expect(createResponse.ok(), await createResponse.text()).toBe(true);
		const agent = await createResponse.json();

		try {
			const deactivateResponse = await api.put(
				`/api/agents/${agent.id}`,
				{
					data: { is_active: false },
				},
			);
			expect(
				deactivateResponse.ok(),
				await deactivateResponse.text(),
			).toBe(true);

			await page.goto("/agents");
			await expect(
				page.getByRole("heading", { name: /agents/i }).first(),
			).toBeVisible({ timeout: 10000 });
			await expect(page.getByText(name)).toHaveCount(0);

			await page.getByRole("switch", { name: "Show Inactive" }).click();
			await expect(page.getByText(name)).toBeVisible();
		} finally {
			await api.delete(`/api/agents/${agent.id}`);
		}
	});

	test("displays fleet stats and agent cards", async ({ page }) => {
		await page.goto("/agents");

		// Heading visible
		await expect(
			page.getByRole("heading", { name: /agents/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Fleet stats present (at least one stat label) — `.first()` since the
		// `.or()` may resolve to multiple visible elements (Runs (7d) AND Avg
		// success rate both render in the FleetStats strip).
		await expect(
			page
				.getByText(/runs \(7d\)/i)
				.or(page.getByText(/success rate/i))
				.first(),
		).toBeVisible({ timeout: 10000 });

		// Either we have agent cards/rows or an empty state. `:visible` keeps
		// the always-hidden mobile metrics strip (also `.grid`, DOM-first) from
		// being picked by `.first()` at the desktop viewport.
		const cardsOrEmpty = page
			.locator(".grid:visible")
			.or(page.getByRole("table"))
			.or(page.getByText(/no agents/i));
		await expect(cardsOrEmpty.first()).toBeVisible();

		await page.screenshot({
			path: "test-results/screenshots/fleet-page.png",
			fullPage: true,
		});
	});

	test("search input filters agents", async ({ page }) => {
		await page.goto("/agents");
		await expect(
			page.getByRole("heading", { name: /agents/i }).first(),
		).toBeVisible({ timeout: 10000 });
		const search = page.getByPlaceholder(/search agents/i);
		if ((await search.count()) > 0) {
			await search.fill("xxxxnonexistentxxxx");
			// After filter, the empty-state heading should appear ("No agents
			// match your search" when there were agents, or "No agents yet"
			// when the fleet was empty to begin with).
			await expect(
				page.getByRole("heading", { name: /no agents/i }).first(),
			).toBeVisible({ timeout: 5000 });
		}
	});

	test("grid/table toggle changes view", async ({ page }) => {
		await page.goto("/agents");
		await expect(
			page.getByRole("heading", { name: /agents/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Look for table view toggle (radio group with "table view" label).
		const tableToggle = page
			.getByLabel(/table view/i)
			.or(page.getByRole("radio", { name: /table/i }));
		if ((await tableToggle.count()) === 0) return;

		await tableToggle.first().click();

		// If agents exist, a real <table> renders. If the fleet is empty, the
		// EmptyState card is shown regardless of view mode — that's still a
		// successful toggle interaction. Accept either outcome.
		await expect(
			page
				.getByRole("table")
				.or(page.getByRole("heading", { name: /no agents/i })),
		).toBeVisible({ timeout: 5000 });
	});
});
