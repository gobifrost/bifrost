import { expect, test } from "./fixtures/api-fixture";

const now = "2026-09-14T12:00:00Z";

function integration(
	id: string,
	name: string,
	statusCounts: Record<string, number>,
) {
	return {
		id,
		name,
		description: `${name} OAuth connection health`,
		list_entities_data_provider_id: null,
		config_schema: [],
		entity_id: null,
		entity_id_name: null,
		default_entity_id: null,
		has_oauth_config: true,
		logo_url: null,
		logo: null,
		logo_version: null,
		mapping_count: 3,
		connected_count:
			(statusCounts.completed ?? 0) + (statusCounts.connected ?? 0),
		needs_reconnection_count: statusCounts.failed ?? 0,
		connection_status_counts: statusCounts,
		is_deleted: false,
		created_at: now,
		updated_at: now,
	};
}

test("integration cards summarize OAuth health and open the breakdown", async ({
	page,
}, testInfo) => {
	await page.setViewportSize({ width: 1440, height: 1000 });
	await page.route("**/api/integrations", async (route) => {
		if (route.request().method() !== "GET") return route.continue();
		await route.fulfill({
			contentType: "application/json",
			body: JSON.stringify({
				total: 4,
				items: [
					integration(
						"00000000-0000-0000-0000-000000000001",
						"Connected example",
						{ completed: 3 },
					),
					integration(
						"00000000-0000-0000-0000-000000000002",
						"Degraded example",
						{ completed: 2, failed: 1 },
					),
					integration(
						"00000000-0000-0000-0000-000000000003",
						"Failed example",
						{ failed: 3 },
					),
					integration(
						"00000000-0000-0000-0000-000000000004",
						"None example",
						{},
					),
				],
			}),
		});
	});

	await page.goto("/integrations");
	for (const status of ["Connected", "Degraded", "Failed", "None"]) {
		await expect(
			page.getByRole("button", {
				name: `OAuth connection health: ${status}`,
			}),
		).toBeVisible();
	}

	const states = await page.screenshot({
		animations: "disabled",
		path: "playwright-results/screenshots/oauth-health-states.png",
	});
	await testInfo.attach("oauth-health-states", {
		body: states,
		contentType: "image/png",
	});

	await page
		.getByRole("button", { name: "OAuth connection health: Degraded" })
		.click();
	const breakdown = page.getByLabel("OAuth connection breakdown");
	await expect(breakdown).toBeVisible();
	await expect(breakdown.getByText("OAuth connections")).toBeVisible();

	const flyout = await page.screenshot({
		animations: "disabled",
		path: "playwright-results/screenshots/oauth-health-degraded-flyout.png",
	});
	await testInfo.attach("oauth-health-degraded-flyout", {
		body: flyout,
		contentType: "image/png",
	});
});
