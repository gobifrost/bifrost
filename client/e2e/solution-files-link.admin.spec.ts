/**
 * Solution → Files link (M8)
 *
 * Verifies the Files row in the Solution Contents tab:
 *   - When a solution has files, the "Files" chip appears in the Contents tab
 *     showing the file count.
 *   - Clicking the chip renders a "Browse Files" link pointing at
 *     /files?install=<id>.
 *   - The Files page (with ?install=) shows the back link to the solution.
 *
 * NOTE: This spec is written but is NOT in CI for this worktree (the
 * Playwright stack only has the debug stack at localhost:34212, not the test
 * stack). The unit contract is covered by the SolutionDetail + FilesExplorer
 * vitest suites. The Playwright spec is provided for manual/future CI use.
 */

import { test, expect } from "./fixtures/api-fixture";

test.describe("Solution Files link (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("Files chip appears in Contents and links to /files?install=<id>", async ({
		page,
		api,
	}) => {
		// Create a bare global solution.
		const slug = `e2e-files-link-${Date.now()}`;
		const createR = await api.post("/api/solutions", {
			data: { slug, name: slug.toUpperCase(), organization_id: null },
		});
		expect(createR.ok()).toBe(true);
		const sol = await createR.json();
		const solId = sol.id as string;

		// Seed an allow-all policy on the 'solutions' location (global).
		await api.put("/api/files/policies/", {
			data: {
				policies: {
					policies: [{ name: "allow_all", actions: ["read", "write", "delete", "list"] }],
				},
			},
			params: { location: "solutions" },
		});

		// Write a file into the solution scope.
		const writeR = await api.post(`/api/files/write?solution=${solId}`, {
			data: {
				location: "solutions",
				path: "data/hello.txt",
				content: "hi",
				mode: "cloud",
			},
		});
		expect(writeR.status()).toBe(204);

		// Navigate to the Solution detail page.
		await page.goto(`/solutions/${solId}`);
		await expect(page.getByTestId("solution-detail")).toBeVisible({ timeout: 15000 });

		// Switch to the Contents tab.
		await page.getByTestId("tab-contents").click();

		// The Files chip must be visible with a count of 1.
		const filesChip = page.getByTestId("chip-files");
		await expect(filesChip).toBeVisible({ timeout: 5000 });
		await expect(filesChip).toContainText("Files");
		await expect(filesChip).toContainText("1");

		// Click the Files chip.
		await filesChip.click();

		// The "Browse Files" link must point at /files?install=<id>.
		const browseLink = page.getByTestId("files-view-link");
		await expect(browseLink).toBeVisible({ timeout: 5000 });
		await expect(browseLink).toHaveAttribute(
			"href",
			`/files?install=${solId}&from=solution:${solId}`,
		);

		// Navigate to the Files page via the link.
		await browseLink.click();
		await expect(page).toHaveURL(new RegExp(`/files\\?install=${solId}`));

		// The back link to the solution must appear.
		await expect(page.getByTestId("files-solution-back")).toBeVisible({ timeout: 10000 });
	});
});
