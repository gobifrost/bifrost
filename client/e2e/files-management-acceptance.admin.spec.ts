import { readFile } from "node:fs/promises";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";
import type { Page } from "./fixtures/api-fixture";

const UNIQUE = `${Date.now()}-${Math.floor(Math.random() * 10000)}`;
const SHARE = `e2e-files-management-${UNIQUE}`.replace(/[^a-z0-9-]/g, "-");
const FILE_NAME = `managed-${UNIQUE}.txt`;
const FILE_CONTENT = `files management acceptance ${UNIQUE}\n`;

type FileStructureResponse = {
	entries?: Array<{ name: string; kind: "folder" | "file"; path: string }>;
};

async function expectOk(
	response: Pick<Awaited<ReturnType<AuthedApi["get"]>>, "ok" | "text">,
) {
	expect(response.ok(), await response.text()).toBe(true);
}

async function expectCleanup(response: Awaited<ReturnType<AuthedApi["post"]>>) {
	expect([200, 204, 404]).toContain(response.status());
}

async function gotoFiles(page: Page) {
	await page.goto("/files");
	await expect(
		page.getByRole("heading", { name: /files/i }).first(),
	).toBeVisible({ timeout: 15_000 });
}

async function createShare(page: Page, name: string) {
	await page.getByRole("button", { name: /new share/i }).click();
	await page.getByLabel(/share name/i).fill(name);
	await page.getByRole("button", { name: /create share/i }).click();
	await expect(page.getByText(name, { exact: false }).first()).toBeVisible({
		timeout: 10_000,
	});
}

async function uploadFile(page: Page) {
	const chooserPromise = page.waitForEvent("filechooser");
	await page
		.getByRole("button", { name: /upload/i })
		.first()
		.click();
	const chooser = await chooserPromise;
	await chooser.setFiles({
		name: FILE_NAME,
		mimeType: "text/plain",
		buffer: Buffer.from(FILE_CONTENT),
	});
	await expect(page.getByText(/upload complete/i)).toBeVisible({
		timeout: 20_000,
	});
}

async function expectFileVisible(page: Page) {
	await expect(async () => {
		if (!(await page.getByText(FILE_NAME).first().isVisible())) {
			await page.getByText(SHARE, { exact: false }).first().click();
		}
		await expect(page.getByText(FILE_NAME).first()).toBeVisible({
			timeout: 3_000,
		});
	}).toPass({ timeout: 30_000 });
}

function fileRow(page: Page) {
	return page.getByRole("list", { name: "Folders and files" }).getByRole("listitem").filter({ hasText: FILE_NAME }).first();
}

async function openFileAction(page: Page, actionName: string | RegExp) {
	const row = fileRow(page);
	await expect(row).toBeVisible();
	await row.getByRole("button", { name: `Actions for ${FILE_NAME}` }).click();
	await page.getByRole("menuitem", { name: actionName }).click();
}

async function listShare(api: AuthedApi) {
	const response = await api.post("/api/files/structure", {
		data: { location: SHARE, prefix: "", scope: "global" },
	});
	await expectOk(response);
	return (await response.json()) as FileStructureResponse;
}

async function fileExists(api: AuthedApi) {
	const structure = await listShare(api);
	return Boolean(
		structure.entries?.some(
			(entry) => entry.kind === "file" && entry.path === FILE_NAME,
		),
	);
}

test.describe("Files management acceptance (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("downloads and deletes an owned file through the explorer UI", async ({
		page,
		api,
	}) => {
		await gotoFiles(page);
		await createShare(page, SHARE);
		await uploadFile(page);
		await expectFileVisible(page);

		await page.getByText(FILE_NAME).first().click();
		await expect(page.getByText(FILE_CONTENT.trim())).toBeVisible({
			timeout: 15_000,
		});
		expect(await fileExists(api)).toBe(true);
		await page
			.getByRole("region", { name: "File details", exact: true })
			.getByRole("button", { name: "Close file details", exact: true })
			.click();

		const downloadPromise = page.waitForEvent("download");
		await openFileAction(page, "Download");
		const download = await downloadPromise;
		expect(download.suggestedFilename()).toBe(FILE_NAME);
		const downloadPath = await download.path();
		expect(downloadPath).toBeTruthy();
		await expect(readFile(downloadPath!, "utf8")).resolves.toBe(
			FILE_CONTENT,
		);

		await openFileAction(page, "Delete");
		const dialog = page.getByRole("alertdialog", { name: "Delete file?" });
		await expect(dialog).toContainText(SHARE);
		await expect(dialog).toContainText(FILE_NAME);
		await dialog.getByRole("button", { name: "Delete file" }).click();
		await expect(page.getByText("File deleted")).toBeVisible({
			timeout: 10_000,
		});
		await expect(page.getByText(FILE_NAME)).not.toBeVisible();

		await page.reload();
		await expect(
			page.getByText(SHARE, { exact: false }).first(),
		).toBeVisible({
			timeout: 10_000,
		});
		await page.getByText(SHARE, { exact: false }).first().click();
		await expect(page.getByText(FILE_NAME)).not.toBeVisible();
		expect(await fileExists(api)).toBe(false);
	});

	test.afterAll(async ({ api }) => {
		await expectCleanup(
			await api.post("/api/files/delete", {
				data: { path: FILE_NAME, location: SHARE, scope: "global" },
			}),
		);
		await expectCleanup(
			await api.delete("/api/files/policies/", {
				params: { location: SHARE },
			}),
		);
	});
});
