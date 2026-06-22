/**
 * Files Explorer — Admin happy-path
 *
 * Drives the redesigned 3-pane Files explorer end-to-end as a platform admin:
 *   - create a share via "New share" (creates the first policy → backend seeds
 *     admin_bypass, so the admin is allowed by a visible, revocable rule)
 *   - the share appears in the tree
 *   - upload a text file → it appears in the listing
 *   - select it → preview shows its text
 *   - open Test Access → the modal renders
 * Runs at desktop and a narrow (mobile) viewport, asserting no horizontal
 * page overflow at the narrow width and that the tree is reachable behind the
 * hamburger sheet.
 */

import { test, expect } from "./fixtures/api-fixture";
import type { Page } from "@playwright/test";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const SHARE = `e2e-explorer-${UNIQUE}`.replace(/[^a-z0-9-]/g, "-");

async function gotoFiles(page: Page) {
	await page.goto("/files");
	await expect(
		page.getByRole("heading", { name: /files/i }).first(),
	).toBeVisible({ timeout: 15000 });
}

async function createShare(page: Page, name: string) {
	await page.getByRole("button", { name: /new share/i }).click();
	await page.getByLabel(/share name/i).fill(name);
	await page.getByRole("button", { name: /create share/i }).click();
	// The dialog closes and the share appears in the tree.
	await expect(page.getByText(name, { exact: false }).first()).toBeVisible({
		timeout: 10000,
	});
}

test.describe("Files Explorer (desktop)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("create share, upload, preview, open Test Access", async ({ page }) => {
		await gotoFiles(page);
		await createShare(page, SHARE);

		// Select the share in the tree.
		await page.getByText(SHARE, { exact: false }).first().click();

		// Upload a text file via the hidden input behind the Upload button.
		const fileChooserPromise = page.waitForEvent("filechooser");
		await page.getByRole("button", { name: /upload/i }).first().click();
		const chooser = await fileChooserPromise;
		await chooser.setFiles({
			name: "hello.txt",
			mimeType: "text/plain",
			buffer: Buffer.from("hello from e2e"),
		});

		// Wait for the success toast so we know complete-upload landed.
		await expect(page.getByText(/upload complete/i)).toBeVisible({
			timeout: 20000,
		});

		// The uploaded file appears in the listing (structural list can lag S3
		// list-after-write briefly; re-select the share to nudge a refetch).
		await expect(async () => {
			if (!(await page.getByText("hello.txt").first().isVisible())) {
				await page.getByText(SHARE, { exact: false }).first().click();
			}
			await expect(page.getByText("hello.txt").first()).toBeVisible({
				timeout: 3000,
			});
		}).toPass({ timeout: 30000 });

		// Selecting it shows the preview text.
		await page.getByText("hello.txt").first().click();
		await expect(page.getByText("hello from e2e")).toBeVisible({
			timeout: 15000,
		});

		// Open Test Access from the effective-access panel.
		await page.getByRole("button", { name: /test access/i }).first().click();
		await expect(
			page.getByRole("dialog").getByText(/test access/i).first(),
		).toBeVisible();
	});
});

test.describe("Files Explorer (narrow)", () => {
	test.use({ viewport: { width: 390, height: 844 } });

	test("tree reachable via hamburger; no horizontal overflow", async ({
		page,
	}) => {
		await gotoFiles(page);

		// The tree is behind a hamburger sheet at this width.
		await page.getByRole("button", { name: /open shares/i }).click();
		await expect(
			page.getByRole("dialog").getByText(/shares/i).first(),
		).toBeVisible();
		// Close the sheet.
		await page.keyboard.press("Escape");

		// No horizontal page overflow.
		const overflow = await page.evaluate(() => {
			const el = document.scrollingElement ?? document.body;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflow).toBeLessThanOrEqual(1);
	});
});
