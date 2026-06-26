/**
 * Solution → embedded Files browser (M8)
 *
 * Verifies the Files inventory in the Solution Contents tab:
 *   - When a solution has files, the "Files" chip appears in the Contents tab
 *     showing the file count.
 *   - Clicking the chip renders the same read-only file browser used by the
 *     Files page, scoped to the Solution install.
 *
 * NOTE: This spec is written but is NOT in CI for this worktree (the
 * Playwright stack only has the debug stack at localhost:34212, not the test
 * stack). The unit contract is covered by the SolutionDetail + FilesExplorer
 * vitest suites. The Playwright spec is provided for manual/future CI use.
 */

import { expect, type Page } from "@playwright/test";
import { Buffer } from "node:buffer";
import { test, type AuthedApi } from "./fixtures/api-fixture";

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

function minimalSolutionZip(slug: string): Buffer {
	const entries = [
		{
			path: "bifrost.solution.yaml",
			content: `slug: ${slug}\nname: ${slug.toUpperCase()}\nglobal_repo_access: false\n`,
		},
		{
			path: ".bifrost/files.yaml",
			content: "locations:\n- solutions\n",
		},
	];
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
		local.writeUInt16LE(0, 6);
		local.writeUInt16LE(0, 8);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(data.length, 18);
		local.writeUInt32LE(data.length, 22);
		local.writeUInt16LE(name.length, 26);
		local.writeUInt16LE(0, 28);
		localParts.push(local, name, data);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt16LE(0, 8);
		central.writeUInt16LE(0, 10);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(data.length, 20);
		central.writeUInt32LE(data.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt16LE(0, 30);
		central.writeUInt16LE(0, 32);
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

async function deployWithSolutionsLocation(
	api: AuthedApi,
	page: Page,
	solId: string,
	slug: string,
) {
	const deployR = await page.context().request.post(`/api/solutions/${solId}/deploy?force=true`, {
		headers: await api.csrfHeader(),
		multipart: {
			file: {
				name: "solution.zip",
				mimeType: "application/zip",
				buffer: minimalSolutionZip(slug),
			},
		},
	});
	if (deployR.status() !== 202) {
		throw new Error(`deploy solution: ${deployR.status()} ${await deployR.text()}`);
	}
	const { deploy_job_id: deployJobId } = (await deployR.json()) as {
		deploy_job_id: string;
	};

	await expect
		.poll(
			async () => {
				const statusR = await api.get(`/api/solutions/deploy-jobs/${deployJobId}`);
				expect(statusR.ok(), `poll deploy job: ${await statusR.text()}`).toBe(true);
				const status = (await statusR.json()) as { status: string; error?: string };
				if (status.status === "failed") {
					throw new Error(status.error || "solution deploy failed");
				}
				return status.status;
			},
			{ timeout: 30000 },
		)
		.toBe("succeeded");
}

test.describe("Solution Files browser (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("Files chip appears in Contents and opens the embedded file browser", async ({
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

		try {
			await deployWithSolutionsLocation(api, page, solId, slug);

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

			// The Solutions catalog card should expose a compact Files count in
			// its responsive footer without needing a per-card entities fetch.
			await page.goto("/solutions");
			const card = page.getByTestId("install-card").filter({ hasText: slug.toUpperCase() });
			await expect(card).toBeVisible({ timeout: 10000 });
			await expect(card.getByTestId("solution-card-counts")).toBeVisible();
			await expect(card.getByTestId("solution-count-files")).toContainText("1");

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

			// The Files browser is embedded in-place and remains on the Solution page.
			await expect(page).toHaveURL(new RegExp(`/solutions/${solId}`));
			await expect(page.getByRole("tree")).toBeVisible({ timeout: 10000 });

			// Select the declared "solutions" file location and confirm the
			// solution-scoped file path is visible through the shared browser UI.
			await page.getByRole("treeitem", { name: /solutions/i }).click();

			await expect(page.getByRole("cell", { name: "data" })).toBeVisible({
				timeout: 10000,
			});
		} finally {
			await api
				.delete(`/api/solutions/${solId}`, { params: { confirm: slug } })
				.catch(() => {});
		}
	});
});
