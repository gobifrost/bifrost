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

		await page
			.getByRole("dialog", { name: "Review workspace import" })
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
			dialog.getByText(/Solutions are designed to work together\./),
		).toBeVisible();
		await expect(dialog.getByText(/need review/)).toBeVisible();
		await expect(dialog.getByText(/Target scope: Global/)).toBeVisible();
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
		expect(nameBox!.x).toBeGreaterThanOrEqual(dialogBox!.x);
		expect(nameBox!.x + nameBox!.width).toBeLessThanOrEqual(
			dialogBox!.x + dialogBox!.width + 1,
		);
		await testInfo.attach("workspace-import-review", {
			body: await dialog.screenshot(),
			contentType: "image/png",
		});
		await dialog.getByRole("button", { name: "Replace All" }).click();
		await expect(dialog.getByText(/of 2 conflicts resolved/)).toBeVisible();
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
