/**
 * Per-mapping OAuth — Smoke Tests (Admin)
 *
 * Exercises the manual per-mapping entity-ID path introduced in the
 * per-mapping-oauth feature branch:
 *
 * 1. When no data provider is configured, the Mappings tab shows a
 *    "No data provider configured" notice and a manual Entity ID input
 *    per row.
 *
 * The Connect-button behavior is component-tested with a deterministic
 * mapping fixture; the backend authorize contract has its own API coverage.
 * The broader behavioral coverage lives in:
 *   - api/tests/unit/test_oauth_state.py
 *   - api/tests/e2e/api/test_per_mapping_oauth.py
 *   - client/src/components/integrations/IntegrationMappingsTab.test.tsx
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Per-mapping OAuth", () => {
	// ---------------------------------------------------------------------------
	// Test 1: Mapping table renders with manual entity_id input (no data provider)
	//
	// Strategy: own a bare integration fixture, navigate directly to it, open the
	// Mappings tab, and assert the manual entity-ID contract. The test must not
	// depend on whatever integrations another test happened to leave behind.
	// ---------------------------------------------------------------------------
	test("mapping table renders on integration detail page", async ({
		page,
		api,
	}) => {
		const created = await api.post("/api/integrations", {
			data: { name: `Mapping smoke ${Date.now()}` },
		});
		expect(created.ok(), await created.text()).toBe(true);
		const integration = (await created.json()) as { id: string };

		try {
			await page.goto(`/integrations/${integration.id}`);

			await expect(page).toHaveURL(/\/integrations\/[0-9a-f-]{36}/i, {
				timeout: 5000,
			});

			const mappingsTab = page.getByRole("tab", { name: /mappings/i });
			await expect(mappingsTab).toBeVisible({ timeout: 5000 });
			await mappingsTab.click();

			await expect(
				page.getByRole("columnheader", { name: /connection/i }),
			).toBeVisible({ timeout: 5000 });

			// This fixture intentionally has no data provider, so the manual entity
			// path is the contract rather than an environment-dependent branch.
			await expect(
				page.getByText(/no data provider configured/i),
			).toBeVisible();
			await expect(
				page.getByPlaceholder(/entity id/i).first(),
			).toBeVisible();
		} finally {
			await api.delete(`/api/integrations/${integration.id}`);
		}
	});
});
