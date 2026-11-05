/** Empty Tune Agent state: evidence controls remain accessible while proposal
 * generation and evaluation are gated until flagged runs exist. The companion
 * apply spec covers proposal review, editing, evaluation and persistence.
 */

import { test, expect } from "./fixtures/api-fixture";
import { seedAgentViaPage } from "./setup/seed-agent";

test.describe("Agent Tuning (admin)", () => {
	test("tuning workbench renders with correct structure and disabled CTAs", async ({
		page,
	}) => {
		const agent = await seedAgentViaPage(page, {
			namePrefix: "Tune Spec",
		});

		await page.goto(`/agents/${agent.id}/tune`);

		// 1. Heading is always present regardless of flagged-run count.
		await expect(
			page.getByRole("heading", { name: /tune agent/i }),
		).toBeVisible({ timeout: 10000 });

		// 2. Supporting evidence is accessible without reserving diff width.
		await expect(
			page.getByRole("button", { name: "Flagged Runs (0)" }),
		).toBeVisible();
		await expect(
			page.getByRole("button", { name: "Evaluation", exact: true }),
		).toBeDisabled();

		// 3. No flagged runs: generation is disabled and the review desk is empty.
		await expect(
			page.getByTestId("generate-proposal-button"),
		).toBeVisible();
		await expect(
			page.getByTestId("generate-proposal-button"),
		).toBeDisabled();
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
