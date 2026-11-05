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
import type { Page } from "./fixtures/api-fixture";

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

	test("create share, upload, preview, open Test Access", async ({
		page,
	}) => {
		await gotoFiles(page);
		await createShare(page, SHARE);

		// Select the share in the tree.
		await page.getByText(SHARE, { exact: false }).first().click();

		// Upload from the selected share menu, committing its destination first.
		const fileChooserPromise = page.waitForEvent("filechooser");
		await page
			.getByRole("button", { name: /upload/i })
			.first()
			.click();
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

		// Details belong to the Files workspace at every breakpoint.
		const inspector = page.getByRole("region", {
			name: "File details",
			exact: true,
		});
		const workspace = page.getByRole("region", {
			name: "Files explorer",
			exact: true,
		});
		for (const width of [1440, 1100, 390]) {
			await page.setViewportSize({ width, height: 900 });
			await expect(inspector).toBeVisible();
			await expect(async () => {
				const pane = await inspector.boundingBox();
				const bounds = await workspace.boundingBox();
				expect(pane).not.toBeNull();
				expect(bounds).not.toBeNull();
				expect(pane!.y).toBeGreaterThanOrEqual(bounds!.y);
				expect(pane!.x).toBeGreaterThanOrEqual(bounds!.x);
				expect(pane!.x + pane!.width).toBeLessThanOrEqual(
					bounds!.x + bounds!.width + 1,
				);
			}).toPass();
			expect(
				await page.evaluate(
					() => document.documentElement.scrollWidth - innerWidth,
				),
			).toBeLessThanOrEqual(1);
		}
		await page.keyboard.press("Escape");
		await expect(inspector).toBeHidden();
		await expect(
			page.getByRole("button", { name: "hello.txt", exact: true }),
		).toBeFocused();
		await page.setViewportSize({ width: 1440, height: 900 });
		await page
			.getByRole("button", { name: "hello.txt", exact: true })
			.click();

		// Access edits the selected path directly; Test has its own tab.
		await page.getByRole("tab", { name: "Access", exact: true }).click();
		await expect(
			inspector.getByRole("heading", {
				name: "Manage Policy",
				exact: true,
			}),
		).toBeVisible();
		await page.getByRole("tab", { name: "Test", exact: true }).click();
		await expect(
			inspector.getByRole("heading", {
				name: "Test Access",
				exact: true,
			}),
		).toBeVisible();
		await expect(page.getByRole("dialog")).toHaveCount(0);
		await inspector
			.getByRole("combobox", { name: "User", exact: true })
			.click();
		await page.getByRole("option").first().click();
		await expect(inspector.getByText(/^(Allowed|Denied)$/)).toHaveCount(4);
		await inspector
			.getByRole("tab", { name: "Access", exact: true })
			.click();
		await expect(
			inspector.getByRole("tab", { name: "Access", exact: true }),
		).toHaveAttribute("aria-selected", "true");
		await inspector
			.getByRole("button", { name: "Close file details" })
			.click();
		await page
			.getByRole("tab", { name: "Access Policies", exact: true })
			.click();
		await page.getByText(SHARE, { exact: true }).first().click();
		await expect(
			page.getByRole("tab", { name: "Access Policies", exact: true }),
		).toHaveAttribute("aria-selected", "true");
		await page
			.getByRole("button", {
				name: `Manage policy for ${SHARE}/`,
				exact: true,
			})
			.click();
		await expect(
			inspector.getByRole("heading", {
				name: "Manage Policy",
				exact: true,
			}),
		).toBeVisible();
		await expect(page.getByRole("dialog")).toHaveCount(0);
		await expect(
			inspector.getByRole("button", { name: /save policy/i }),
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
			page
				.getByRole("dialog")
				.getByText(/shares/i)
				.first(),
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
