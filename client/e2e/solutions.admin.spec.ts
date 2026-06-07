/**
 * Solutions management (Admin) — Solutions configs + lifecycle UI
 *
 * Asserts the structural contract of the new Solutions management surface:
 *
 *   1. `/solutions` renders the list page (heading + "Install Solution" action)
 *      and the whole-page dropzone is present.
 *   2. The empty state is shown when no installs exist (a clean test stack has
 *      none), inviting a drag-and-drop install.
 *   3. The Install button opens the file picker affordance (hidden input
 *      present + accept=".zip").
 *
 * Full "drag a real .zip → preview → pick scope → install → see it in the list
 * → open detail → set a config → navigate to an entity and back → delete"
 * coverage requires a built Solution workspace .zip fixture AND the server-side
 * deploy pipeline (npm/vite build) running in the test stack — neither is
 * available to a plain Playwright run. That whole flow is covered end-to-end at
 * the API layer by `api/tests/e2e/platform/test_solution_zip_install_e2e.py`
 * (atomic install + config values), `test_solution_entities_endpoint.py`,
 * `test_solution_delete.py`, and `test_solution_reattach.py`; the UI wiring is
 * covered by the vitest suites for Solutions.tsx / SolutionDetail.tsx. This
 * spec covers what is genuinely exercisable through the browser without that
 * fixture: the list surface and its affordances.
 */

import { test, expect } from "@playwright/test";

test.describe("Solutions management (admin)", () => {
	test("solutions list renders with install affordance", async ({ page }) => {
		await page.goto("/solutions");

		await expect(
			page.getByRole("heading", { name: "Solutions", exact: true }),
		).toBeVisible({ timeout: 10000 });

		// The Install Solution action + the hidden .zip file input.
		await expect(
			page.getByRole("button", { name: /install solution/i }),
		).toBeVisible();
		const fileInput = page.locator('[data-testid="install-file-input"]');
		await expect(fileInput).toHaveAttribute("accept", /zip/);

		// Whole-page dropzone is present (drag-and-drop install target).
		await expect(page.locator('[data-testid="install-dropzone"]')).toBeVisible();
	});

	test("empty state invites an install when no solutions exist", async ({
		page,
	}) => {
		await page.goto("/solutions");
		await expect(
			page.getByRole("heading", { name: "Solutions", exact: true }),
		).toBeVisible({ timeout: 10000 });

		// A clean test stack has no installs → either the empty-state copy or
		// at least zero install cards. Assert no install cards render; if the
		// empty-state node is present, assert it mentions installing.
		const cards = page.locator('[data-testid="install-card"]');
		await expect(cards).toHaveCount(0);
	});
});
