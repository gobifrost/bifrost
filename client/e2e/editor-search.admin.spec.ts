import { test, expect } from "./fixtures/api-fixture";

const MATCH_COUNT = 130; // more than one 100-match page

test.describe("Editor source search (admin)", () => {
	test("pages through every match with Load more", async ({ page, api }) => {
		const token = `E2EEDSEARCH${Date.now()}${Math.floor(Math.random() * 10000)}`;
		const folder = `e2e-editor-search-${token}`;
		// One file with a match per line: 130 matches without 130 seed writes.
		const response = await api.post("/api/files/write", {
			data: {
				path: `${folder}/many.txt`,
				content: `${token}\n`.repeat(MATCH_COUNT),
				mode: "cloud",
				location: "workspace",
			},
		});
		expect(response.status(), await response.text()).toBe(204);

		try {
			await page.goto("/");
			await page.getByRole("button", { name: "Shell (Cmd+/)" }).click();
			const dialog = page.getByRole("dialog", { name: "Code editor" });
			await dialog.getByRole("button", { name: "Search" }).click();
			await dialog
				.getByRole("textbox", { name: "Search file contents" })
				.fill(token);
			await dialog
				.getByRole("textbox", { name: "Search file contents" })
				.press("Enter");

			const results = dialog.getByRole("region", {
				name: "Search results",
			});
			await expect(results.getByRole("status")).toHaveText(
				`100+ matches for “${token}”`,
			);
			await expect(results.getByRole("listitem")).toHaveCount(100);

			await results
				.getByRole("button", { name: "Load more results" })
				.click();
			await expect(results.getByRole("status")).toHaveText(
				`${MATCH_COUNT} matches for “${token}”`,
			);
			await expect(results.getByRole("listitem")).toHaveCount(
				MATCH_COUNT,
			);
			await expect(
				results.getByRole("button", { name: "Load more results" }),
			).toHaveCount(0);
		} finally {
			const response = await api.delete("/api/files/editor", {
				params: { path: folder },
			});
			expect([200, 204, 404]).toContain(response.status());
		}
	});
});
