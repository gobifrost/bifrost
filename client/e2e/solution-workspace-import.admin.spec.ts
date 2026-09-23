import { Buffer } from "node:buffer";
import { randomUUID } from "node:crypto";
import { test, expect, type AuthedApi } from "./fixtures/api-fixture";

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
	let c = n;
	for (let k = 0; k < 8; k += 1) {
		c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
	}
	return c >>> 0;
});

function crc32(input: Buffer): number {
	let crc = 0xffffffff;
	for (const byte of input) {
		crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
	}
	return (crc ^ 0xffffffff) >>> 0;
}

function buildZip(entries: { path: string; content: string }[]): Buffer {
	const localParts: Buffer[] = [];
	const centralParts: Buffer[] = [];
	let offset = 0;

	for (const entry of entries) {
		const name = Buffer.from(entry.path);
		const data = Buffer.from(entry.content);
		const checksum = crc32(data);
		const local = Buffer.alloc(30);
		local.writeUInt32LE(0x04034b50, 0);
		local.writeUInt16LE(20, 4);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(data.length, 18);
		local.writeUInt32LE(data.length, 22);
		local.writeUInt16LE(name.length, 26);
		localParts.push(local, name, data);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(data.length, 20);
		central.writeUInt32LE(data.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt32LE(offset, 42);
		centralParts.push(central, name);
		offset += local.length + name.length + data.length;
	}

	const centralDirectory = Buffer.concat(centralParts);
	const end = Buffer.alloc(22);
	end.writeUInt32LE(0x06054b50, 0);
	end.writeUInt16LE(entries.length, 8);
	end.writeUInt16LE(entries.length, 10);
	end.writeUInt32LE(centralDirectory.length, 12);
	end.writeUInt32LE(offset, 16);
	return Buffer.concat([...localParts, centralDirectory, end]);
}

async function writeFile(api: AuthedApi, path: string, content: string) {
	const response = await api.put("/api/files/editor/content", {
		data: { path, content, encoding: "utf-8" },
	});
	expect(response.ok(), `write ${path}: ${await response.text()}`).toBe(true);
}

async function readFile(api: AuthedApi, path: string): Promise<string> {
	const response = await api.get("/api/files/editor/content", {
		params: { path },
	});
	expect(response.ok(), `read ${path}: ${await response.text()}`).toBe(true);
	return ((await response.json()) as { content: string }).content;
}

test.use({ viewport: { width: 1440, height: 900 } });

test("managed Solution ZIP dialog has room for its install options", async ({ page }, testInfo) => {
	await page.goto("/solutions");
	await page.getByRole("button", { name: "Install Solution" }).click();
	await page.getByTestId("destination-solution").click();
	await page.getByTestId("source-zip").click();
	const dialog = page.getByTestId("solution-dialog");
	await expect(dialog.getByTestId("dialog-dropzone")).toBeVisible();
	await expect.poll(async () => (await dialog.boundingBox())?.width).toBeGreaterThanOrEqual(570);
	await testInfo.attach("solution-install-upload", {
		body: await dialog.screenshot(),
		contentType: "image/png",
	});
});

test("scrolls a long workspace import entity list", async ({ page }, testInfo) => {
	let releasePreview = () => {};
	const previewGate = new Promise<void>((resolve) => { releasePreview = resolve; });
	await page.route("**/api/solutions/import-workspace/preview", async (route) => {
		await previewGate;
		await route.fulfill({
			json: {
				preview_token: "scroll-layout-preview",
				package_name: "Scroll review",
				package_sha256: "a".repeat(64),
				items: Array.from({ length: 60 }, (_, index) => ({
					id: `file:notes/review-${String(index).padStart(2, "0")}.txt`,
					kind: "file",
					name: `notes/review-${String(index).padStart(2, "0")}.txt`,
					classification: "create",
					group_key: null,
				})),
			},
		});
	});

	await page.goto("/solutions");
	await page.getByRole("button", { name: "Install Solution" }).click();
	await page.getByTestId("destination-workspace").click();
	await page.getByTestId("source-zip").click();
	await page.getByRole("dialog", { name: "Import into workspace" })
		.locator('input[type="file"]')
		.setInputFiles({ name: "scroll-review.zip", mimeType: "application/zip", buffer: Buffer.from("layout fixture") });
	const loadingDialog = page.getByRole("dialog", { name: "Import into workspace" });
	await expect(loadingDialog.getByText("Reading package…")).toBeVisible();
	await expect(loadingDialog.getByTestId("workspace-import-footer")).not.toHaveClass(/border-t/);
	await testInfo.attach("workspace-import-loading", {
		body: await loadingDialog.screenshot(),
		contentType: "image/png",
	});
	releasePreview();

	const dialog = page.getByRole("dialog", { name: "Review workspace import" });
	await expect(dialog.getByText("of 60 items")).toBeVisible();
	const scroller = dialog.getByTestId("workspace-import-scroller").locator(".overflow-auto");
	await expect.poll(() => scroller.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
	await scroller.evaluate((element) => { element.scrollTop = element.scrollHeight; });
	await expect.poll(() => scroller.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
	await expect(dialog.getByText("notes/review-59.txt")).toBeVisible();
	await expect(dialog.getByRole("button", { name: "Start import job" })).toBeInViewport();
	await testInfo.attach("workspace-import-long-list-bottom", {
		body: await page.screenshot(),
		contentType: "image/png",
	});
});

test("reviews collisions and replaces workspace content without installing a Solution", async ({
	page,
	api,
}, testInfo) => {
	const suffix = randomUUID().replaceAll("-", "");
	const functionName = `workspace_import_${suffix}`;
	const path = `workflows/${functionName}.py`;
	const existingSource = `from bifrost import workflow\n\n@workflow(name="${functionName}")\nasync def ${functionName}() -> dict:\n    return {"source": "existing"}\n`;
	const importedSource = existingSource.replace('"existing"', '"imported"');
	const workflowId = randomUUID();
	const configKey = `WORKSPACE_IMPORT_TOKEN_${suffix.toUpperCase()}`;

	await writeFile(api, path, existingSource);
	const register = await api.post("/api/workflows/register", {
		data: { path, function_name: functionName, organization_id: null },
	});
	expect(register.ok(), `register workflow: ${await register.text()}`).toBe(
		true,
	);

	const archive = buildZip([
		{
			path: "bifrost.solution.yaml",
			content: JSON.stringify({
				slug: `workspace-import-${suffix}`,
				name: "Workspace Import Review",
				version: "1.0.0",
			}),
		},
		{ path, content: importedSource },
		{
			path: ".bifrost/workflows.yaml",
			content: JSON.stringify({
				workflows: {
					[workflowId]: {
						id: workflowId,
						name: functionName,
						path,
						function_name: functionName,
						organization_id: null,
					},
				},
			}),
		},
		{
			path: ".bifrost/configs.yaml",
			content: JSON.stringify({ configs: {
				[configKey]: { key: configKey, type: "secret", required: true, description: "Import token" },
			} }),
		},
	]);

	try {
		await page.goto("/solutions");
		await page.getByRole("button", { name: "Install Solution" }).click();

		// Screen 1 — destination. Exactly two equally weighted choices.
		const destination = page.getByTestId("destination-picker");
		await expect(destination).toBeVisible();
		await testInfo.attach("workspace-import-destination", {
			body: await page.getByRole("dialog").screenshot(),
			contentType: "image/png",
		});
		await page.getByTestId("destination-workspace").click();

		// Screen 2 — source. Same two origins for either destination.
		const source = page.getByTestId("source-picker");
		await expect(source).toBeVisible();
		await testInfo.attach("workspace-import-source", {
			body: await page.getByRole("dialog").screenshot(),
			contentType: "image/png",
		});
		await page.getByTestId("source-zip").click();
		const uploadDialog = page.getByRole("dialog", { name: "Import into workspace" });
		await expect(uploadDialog.getByRole("combobox", { name: "Target scope" })).toBeVisible();
		await testInfo.attach("workspace-import-upload", {
			body: await uploadDialog.screenshot(),
			contentType: "image/png",
		});

		await page
			.getByRole("dialog", { name: "Import into workspace" })
			.locator('input[type="file"]')
			.setInputFiles({
				name: "workspace-import-review.zip",
				mimeType: "application/zip",
				buffer: archive,
			});

		const dialog = page.getByRole("dialog", {
			name: "Review workspace import",
		});
		await expect(dialog).toBeVisible();
		await expect(
			dialog.getByText(/Keep and Replace decisions can affect other workspace content\./),
		).toBeVisible();
		await expect(dialog.getByText(/1 item needs review/)).toBeVisible();
		await expect(dialog.getByRole("combobox", { name: "Target scope" })).toBeVisible();
		await expect(dialog.getByTestId("workspace-import-scope")).toContainText("Global");
		const configInput = dialog.getByLabel(new RegExp(configKey));
		await expect(configInput).toHaveAttribute("type", "password");
		await expect(configInput).toHaveAttribute("aria-required", "true");
		await expect(dialog.getByRole("button", { name: "Start import job" })).toBeDisabled();
		await expect(dialog.getByText(/you can still import/i)).toHaveCount(0);
		await configInput.fill("browser-test-token");
		await dialog.getByRole("combobox", { name: "Target scope" }).click();
		await page.getByRole("option", { name: /Bifrost Dev Org/ }).click();
		await expect(dialog.getByText("Replace moves this item to the selected scope")).toBeVisible();
		await expect(dialog.getByText(/1 item needs review/)).toBeVisible();
		await dialog.getByRole("combobox", { name: "Target scope" }).click();
		await page.getByRole("option", { name: /Global/ }).click();
		await expect(dialog.getByText("Replace moves this item to the selected scope")).toHaveCount(0);
		await expect(dialog.getByRole("button", { name: "Start import job" })).toBeDisabled();
		await dialog.getByLabel(new RegExp(configKey)).fill("browser-test-token");
		// The full 64-char definition name must fit inside the dialog box —
		// wrapping is fine, horizontal spill is not.
		const name = dialog.getByText(functionName, { exact: true });
		await expect(name).toBeVisible();
		const [nameBox, dialogBox] = await Promise.all([
			name.boundingBox(),
			dialog.boundingBox(),
		]);
		expect(nameBox, "definition name has layout").not.toBeNull();
		expect(dialogBox, "dialog has layout").not.toBeNull();
		expect(dialogBox!.width, "review dialog uses the available desktop width").toBeGreaterThan(760);
		expect(nameBox!.x).toBeGreaterThanOrEqual(dialogBox!.x);
		expect(nameBox!.x + nameBox!.width).toBeLessThanOrEqual(
			dialogBox!.x + dialogBox!.width + 1,
		);
		await testInfo.attach("workspace-import-review", {
			body: await dialog.screenshot(),
			contentType: "image/png",
		});
		await page.setViewportSize({ width: 390, height: 844 });
		await expect(dialog.getByRole("combobox", { name: "Target scope" })).toBeVisible();
		expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
		const mobileReview = dialog.locator("[data-testid=workspace-import-config-section]").locator("..");
		await mobileReview.evaluate((element) => { element.scrollTop = element.scrollHeight; });
		await expect(dialog.getByTestId("workspace-import-scroller").getByText(configKey, { exact: true })).toBeInViewport();
		const [mobileDialogBox, mobileHeaderBox, mobileFooterBox, mobileStartBox] = await Promise.all([
			dialog.boundingBox(),
			dialog.getByRole("heading", { name: "Review workspace import" }).boundingBox(),
			dialog.getByTestId("workspace-import-footer").boundingBox(),
			dialog.getByRole("button", { name: "Start import job" }).boundingBox(),
		]);
		expect(mobileDialogBox).not.toBeNull();
		expect(mobileHeaderBox!.y).toBeGreaterThanOrEqual(mobileDialogBox!.y);
		expect(mobileFooterBox!.y + mobileFooterBox!.height).toBeLessThanOrEqual(mobileDialogBox!.y + mobileDialogBox!.height + 1);
		expect(mobileStartBox!.y + mobileStartBox!.height).toBeLessThanOrEqual(mobileDialogBox!.y + mobileDialogBox!.height + 1);
		await testInfo.attach("workspace-import-review-mobile", {
			body: await page.screenshot(),
			contentType: "image/png",
		});
		await dialog.getByRole("button", { name: "Replace All" }).click();
		await expect(dialog.getByText("All reviewed")).toBeVisible();
		await dialog.getByRole("button", { name: "Start import job" }).click();
		await expect(dialog).not.toBeVisible();

		await expect
			.poll(() => readFile(api, path), { timeout: 60_000 })
			.toBe(importedSource);
		await expect(
			page.getByText("Workspace Import Review", { exact: true }),
		).not.toBeVisible();
	} finally {
		const remove = await api.delete("/api/files/editor", {
			params: { path },
		});
		expect([200, 204, 404]).toContain(remove.status());
	}
});
