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

async function deploySolution(
	api: AuthedApi,
	page: Page,
	solutionId: string,
	slug: string,
) {
	const response = await page.context().request.post(
		`/api/solutions/${solutionId}/deploy?force=true`,
		{
			headers: await api.csrfHeader(),
			multipart: {
				file: {
					name: "solution.zip",
					mimeType: "application/zip",
					buffer: minimalSolutionZip(slug),
				},
			},
		},
	);
	if (response.status() !== 202) {
		throw new Error(`deploy solution: ${response.status()} ${await response.text()}`);
	}

	const { deploy_job_id: deployJobId } = (await response.json()) as {
		deploy_job_id: string;
	};
	await expect
		.poll(
			async () => {
				const statusResponse = await api.get(`/api/solutions/deploy-jobs/${deployJobId}`);
				expect(statusResponse.ok(), `poll deploy job: ${await statusResponse.text()}`).toBe(
					true,
				);
				const status = (await statusResponse.json()) as {
					status: string;
					error?: string;
				};
				if (status.status === "failed") {
					throw new Error(status.error || "solution deploy failed");
				}
				return status.status;
			},
			{ timeout: 30000 },
		)
		.toBe("succeeded");
}

async function seedSolutionFile(api: AuthedApi, solutionId: string) {
	const policyResponse = await api.put("/api/files/policies/", {
		data: {
			policies: {
				policies: [{ name: "allow_all", actions: ["read", "write", "delete", "list"] }],
			},
		},
		params: { location: "solutions" },
	});
	expect(policyResponse.ok(), `seed file policy: ${await policyResponse.text()}`).toBe(true);

	const writeResponse = await api.post(`/api/files/write?solution=${solutionId}`, {
		data: {
			location: "solutions",
			path: "backup/hello.txt",
			content: "hello from backup export e2e",
			mode: "cloud",
		},
	});
	expect(writeResponse.status(), `seed solution file: ${await writeResponse.text()}`).toBe(204);
}

test.describe("Solution backup export (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("queues a scheduler-owned backup export and downloads it when completed", async ({
		page,
		api,
	}) => {
		test.setTimeout(90000);

		const slug = `e2e-backup-export-${Date.now()}`;
		let solutionId = "";
		const createResponse = await api.post("/api/solutions", {
			data: { slug, name: slug.toUpperCase(), organization_id: null },
		});
		expect(createResponse.ok(), `create solution: ${await createResponse.text()}`).toBe(true);
		const solution = (await createResponse.json()) as { id: string };
		solutionId = solution.id;

		try {
			await deploySolution(api, page, solutionId, slug);
			await seedSolutionFile(api, solutionId);

			await page.goto(`/solutions/${solutionId}`);
			await expect(page.getByTestId("solution-detail")).toBeVisible({ timeout: 15000 });

			await page.getByRole("button", { name: "More solution actions" }).click();
			await page.getByRole("menuitem", { name: "Export Solution" }).click();

			const dialog = page.getByRole("dialog", { name: "Export Solution" });
			await expect(dialog).toBeVisible();
			await dialog.getByRole("radio", { name: "Backup" }).click();
			await dialog.getByLabel("Password").fill("correct horse battery staple");
			await expect(dialog.getByRole("checkbox", { name: "Config values" })).toBeChecked();
			await expect(dialog.getByRole("checkbox", { name: "Solution-owned files" })).toBeChecked();
			await dialog.getByRole("button", { name: "Queue backup" }).click();

			const exportsTab = page.getByRole("tab", { name: "Exports" });
			await expect(exportsTab).toHaveAttribute("aria-selected", "true", { timeout: 10000 });

			let exportJobId = "";
			await expect
				.poll(
					async () => {
						const jobsResponse = await api.get(`/api/solutions/${solutionId}/export-jobs`);
						expect(jobsResponse.ok(), `list export jobs: ${await jobsResponse.text()}`).toBe(true);
						const body = (await jobsResponse.json()) as {
							jobs?: { id: string; status: string }[];
						};
						exportJobId = body.jobs?.[0]?.id ?? "";
						return exportJobId;
					},
					{ timeout: 10000 },
				)
				.not.toBe("");

			await expect(page.getByRole("button", { name: "Download" })).toBeDisabled();

			await expect
				.poll(
					async () => {
						const jobResponse = await api.get(`/api/solutions/export-jobs/${exportJobId}`);
						expect(jobResponse.ok(), `poll export job: ${await jobResponse.text()}`).toBe(true);
						const job = (await jobResponse.json()) as {
							status: string;
							failure_message?: string | null;
						};
						if (job.status === "failed") {
							throw new Error(job.failure_message || "solution backup export failed");
						}
						return job.status;
					},
					{ timeout: 60000 },
				)
				.toBe("completed");

			await page.reload();
			await expect(page.getByTestId("solution-detail")).toBeVisible({ timeout: 15000 });
			await page.getByRole("tab", { name: "Exports" }).click();
			await expect(page.getByText("completed", { exact: true })).toBeVisible({
				timeout: 10000,
			});

			const downloadButton = page.getByRole("button", { name: "Download" });
			await expect(downloadButton).toBeEnabled();
			const [download] = await Promise.all([
				page.waitForEvent("download"),
				downloadButton.click(),
			]);
			expect(download.suggestedFilename()).toMatch(/\.zip$/);
		} finally {
			if (solutionId) {
				await api
					.delete(`/api/solutions/${solutionId}`, { params: { confirm: slug } })
					.catch(() => {});
			}
		}
	});
});
