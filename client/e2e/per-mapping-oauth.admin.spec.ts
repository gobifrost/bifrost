/**
 * Per-mapping OAuth — Smoke Tests (Admin)
 *
 * Exercises the two key per-mapping OAuth UI flows introduced in the
 * per-mapping-oauth feature branch:
 *
 * 1. When no data provider is configured, the Mappings tab shows a
 *    "No data provider configured" notice and a manual Entity ID input
 *    per row.
 *
 * 2. When a mapping row has an OAuth provider but no connected token,
 *    a Connect button appears and clicking it triggers the authorize
 *    redirect flow.
 *
 * These are smoke tests. The full behavioral coverage lives in:
 *   - api/tests/unit/test_oauth_state.py
 *   - api/tests/e2e/api/test_per_mapping_oauth.py
 *   - client/src/components/integrations/IntegrationMappingsTab.test.tsx
 */

import { test, expect } from "@playwright/test";

test.describe("Per-mapping OAuth", () => {
	// ---------------------------------------------------------------------------
	// Test 1: Mapping table renders with manual entity_id input (no data provider)
	//
	// Strategy: navigate to the integrations list, click the first integration,
	// open the Mappings tab, and assert the per-mapping OAuth UI is present.
	// The assertion branches on whether the integration has a data provider or not
	// so the test is resilient to any integration that happens to exist in the
	// test environment.
	// ---------------------------------------------------------------------------
	test("mapping table renders on integration detail page", async ({
		page,
	}) => {
		await page.goto("/integrations");

		// Wait for the integrations list to load
		await expect(
			page.getByRole("heading", { name: /integrations/i }).first(),
		).toBeVisible({ timeout: 10000 });

		// Find the first integration row/link and click it
		const firstRow = page
			.locator("table tbody tr")
			.first();

		const hasRows =
			(await firstRow.count()) > 0 &&
			(await firstRow.isVisible().catch(() => false));

		if (!hasRows) {
			// No integrations in the test environment — skip gracefully.
			// To enable: seed at least one integration via the CLI or API before
			// running: `bifrost integrations create --name "Smoke Test" --base-url http://example.com`
			test.skip(true, "No integrations seeded in test environment");
			return;
		}

		await firstRow.click();

		// Should land on the integration detail page
		await expect(page).toHaveURL(/\/integrations\/[0-9a-f-]{36}/i, {
			timeout: 5000,
		});

		// Navigate to the Mappings tab
		const mappingsTab = page.getByRole("tab", { name: /mappings/i });
		await expect(mappingsTab).toBeVisible({ timeout: 5000 });
		await mappingsTab.click();

		// The Connection column header should always be present (introduced by
		// this feature branch — verifies the new column is rendered).
		await expect(
			page.getByRole("columnheader", { name: /connection/i }),
		).toBeVisible({ timeout: 5000 });

		// If no data provider is configured, the notice and manual input appear.
		const hasNoDataProviderNotice = await page
			.getByText(/no data provider configured/i)
			.isVisible()
			.catch(() => false);

		if (hasNoDataProviderNotice) {
			// The notice should link to the manual-input path
			await expect(
				page.getByText(/no data provider configured/i),
			).toBeVisible();

			// Each org row should show a plain text input for the entity ID
			const entityIdInput = page.getByPlaceholder(/entity id/i).first();
			await expect(entityIdInput).toBeVisible();
		}
		// If a data provider IS configured, the select / auto-match UI is shown
		// instead — that's fine; the Connection column presence is the key assertion.
	});

	// ---------------------------------------------------------------------------
	// Test 2: Connect button redirects to the OAuth authorize URL
	//
	// This test requires a specific setup:
	//   - An integration with an OAuth provider configured
	//   - At least one mapping row whose connection_status is NOT "completed"
	//     (so the Connect button is rendered rather than the Disconnect button)
	//
	// That combination isn't guaranteed by the shared test-stack seed data, so
	// the test is skipped by default. To enable:
	//
	//   1. Create an integration with an OAuth provider via the UI or CLI.
	//   2. Add a mapping for at least one organization.
	//   3. Do NOT click Connect so the token is absent.
	//   4. Remove the test.skip() call below.
	//
	// The authorize endpoint is mocked so no real upstream provider is needed.
	// ---------------------------------------------------------------------------
	test.skip(
		"Connect button on mapping row redirects to authorize URL",
		async ({ page }) => {
			// TODO: seed an integration + mapping that surfaces the Connect button,
			// then remove the test.skip() above.

			await page.goto("/integrations");
			await expect(
				page.getByRole("heading", { name: /integrations/i }).first(),
			).toBeVisible({ timeout: 10000 });

			// Navigate to an integration that has an OAuth provider but an
			// unconnected mapping. This part needs a concrete integration name
			// once the seed is in place.
			await page
				.getByRole("link", { name: /test integration with oauth/i })
				.click();
			await page.getByRole("tab", { name: /mappings/i }).click();

			// Intercept the authorize POST so we don't hit a real upstream provider.
			// The per-mapping authorize endpoint is POST /api/integrations/:id/mappings/:mappingId/authorize
			await page.route("**/mappings/*/authorize", (route) =>
				route.fulfill({
					status: 200,
					contentType: "application/json",
					body: JSON.stringify({
						authorization_url: "https://example.com/authz?state=test",
					}),
				}),
			);

			// Wait for the redirect that the Connect handler triggers
			const navPromise = page.waitForURL(/example\.com\/authz/, {
				timeout: 5000,
			});

			// Find and click the Connect button on the first unconnected mapping row
			await page
				.getByRole("button", { name: /^connect$/i })
				.first()
				.click();

			await navPromise;
		},
	);
});
