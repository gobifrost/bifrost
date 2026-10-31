/**
 * Agent Tuning Workbench (Admin)
 *
 * Seeds an agent (no flagged runs) and navigates to its tune page. Asserts
 * the workbench structure: the "Tune agent" heading, the two panes
 * (flagged-runs, editor), and both primary CTAs rendered in their disabled
 * empty-state (no flagged runs → generate and dry-run are gated).
 *
 * The full generate → edit → dry-run → apply lifecycle is covered by the
 * backend E2E at `api/tests/e2e/api/test_agent_management_m1.py`.
 */

import { test, expect } from "./fixtures/api-fixture";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Tuning (admin)", () => {
	test("tuning workbench renders with correct structure and disabled CTAs", async ({ page }) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Tune Spec",
		});

		await page.goto(`/agents/${agent.id}/tune`);

		// 1. Heading is always present regardless of flagged-run count.
		await expect(
			page.getByRole("heading", { name: /tune agent/i }),
		).toBeVisible({ timeout: 10000 });

		// 2. Both panes are present.
		await expect(page.getByTestId("tune-pane-flagged")).toBeVisible();
		await expect(page.getByTestId("tune-pane-editor")).toBeVisible();

		// 3. Generate-proposal button lives in the left pane and is disabled
		//    because no flagged runs have been seeded. The editor shows a
		//    passive placeholder pointing at the left-pane button.
		await expect(page.getByTestId("generate-proposal-button")).toBeVisible();
		await expect(page.getByTestId("generate-proposal-button")).toBeDisabled();
		await expect(page.getByTestId("editor-empty-state")).toBeVisible();

		// 4. Dry-run button is visible but disabled for the same reason.
		await expect(page.getByTestId("dryrun-button")).toBeVisible();
		await expect(page.getByTestId("dryrun-button")).toBeDisabled();

		// 5. Screenshot for visual reference.
		await page.screenshot({
			path: "test-results/screenshots/agent-tune.png",
			fullPage: true,
		});
	});
});
