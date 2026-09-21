import { Buffer } from "node:buffer";
import { type APIRequestContext } from "@playwright/test";
import { test as base, expect, type AuthedApi } from "./fixtures/api-fixture";

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

function minimalSolutionZip(slug: string): Buffer {
	return buildZip([
		{
			path: "bifrost.solution.yaml",
			content: JSON.stringify({
				slug,
				name: slug.toUpperCase(),
				global_repo_access: false,
				version: "1.0.0",
			}),
		},
		{
			path: ".bifrost/files.yaml",
			content: JSON.stringify({ locations: ["solutions"] }),
		},
	]);
}

async function deploySolution(
	api: AuthedApi,
	request: APIRequestContext,
	solutionId: string,
	slug: string,
) {
	const response = await request.post(
		`/api/solutions/${solutionId}/deploy?force=true`,
		{
			headers: await api.csrfHeader(),
			multipart: {
				file: {
					name: `${slug}.zip`,
					mimeType: "application/zip",
					buffer: minimalSolutionZip(slug),
				},
			},
		},
	);
	expect(response.status(), `deploy solution: ${await response.text()}`).toBe(
		202,
	);
	const { deploy_job_id: deployJobId } = (await response.json()) as {
		deploy_job_id: string;
	};
	await expectDeployJobSucceeded(api, deployJobId);
}

async function expectDeployJobSucceeded(api: AuthedApi, deployJobId: string) {
	await expect
		.poll(
			async () => {
				const statusResponse = await api.get(
					`/api/solutions/deploy-jobs/${deployJobId}`,
				);
				expect(
					statusResponse.ok(),
					`poll deploy job: ${await statusResponse.text()}`,
				).toBe(true);
				const status = (await statusResponse.json()) as {
					status: string;
					error?: string | null;
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

async function expectPersistedStatus(
	api: AuthedApi,
	solutionId: string,
	status: "active" | "inactive",
) {
	await expect
		.poll(async () => {
			const response = await api.get(`/api/solutions/${solutionId}`);
			expect(
				response.ok(),
				`get solution: ${await response.text()}`,
			).toBe(true);
			const solution = (await response.json()) as { status: string };
			return solution.status;
		})
		.toBe(status);
}

type LifecycleSolution = {
	solutionId: string;
	slug: string;
	solutionName: string;
};
const test = base.extend<{
	solution: LifecycleSolution;
	solutionStatus: "active" | "inactive";
}>({
	solutionStatus: ["active", { option: true }],
	solution: [
		async ({ api, request, solutionStatus }, provideSolution) => {
			const slug = `e2e-lifecycle-${crypto.randomUUID()}`;
			const solutionName = slug.toUpperCase();
			const response = await api.post("/api/solutions", {
				data: {
					slug,
					name: solutionName,
					organization_id: null,
					global_repo_access: false,
				},
			});
			expect(response.ok(), "create owned solution").toBe(true);
			const { id: solutionId } = (await response.json()) as {
				id: string;
			};
			try {
				await deploySolution(api, request, solutionId, slug);
				await expectPersistedStatus(api, solutionId, "active");
				if (solutionStatus === "inactive") {
					const uninstall = await api.post(
						`/api/solutions/${solutionId}/uninstall`,
					);
					expect(uninstall.ok(), "seed inactive solution").toBe(true);
					await expectPersistedStatus(api, solutionId, "inactive");
				}
				await provideSolution({ solutionId, slug, solutionName });
			} finally {
				const removed = await api.delete(
					`/api/solutions/${solutionId}`,
					{ params: { confirm: slug } },
				);
				expect([200, 204, 404], "remove owned solution").toContain(
					removed.status(),
				);
			}
		},
		{ timeout: 30000 },
	],
});
test.use({ viewport: { width: 1440, height: 900 } });

test("opens the managed Git connection workflow for a manual solution", async ({
	page,
	solution: { solutionId, solutionName },
}) => {
	await page.goto(`/solutions/${solutionId}`);
	await expect(page.getByRole("heading", { name: solutionName })).toBeVisible({
		timeout: 15000,
	});

	await page.getByRole("button", { name: "Connect Git" }).click();
	await expect(
		page.getByRole("dialog", { name: "Edit Solution" }),
	).toBeVisible();
	await expect(
		page.getByText(/repository connection/i),
	).toBeVisible();
});

test("uninstalls a solution and reveals it with Show Inactive", async ({
	page,
	api,
	solution: { solutionId, slug, solutionName },
}) => {
	await page.goto(`/solutions/${solutionId}`);
	await expect(page.getByRole("heading", { name: solutionName })).toBeVisible(
		{
			timeout: 15000,
		},
	);
	await expect(
		page.getByRole("button", { name: "Update", exact: true }),
	).toBeVisible();
	await expect(page.getByText("Inactive", { exact: true })).not.toBeVisible();

	await page.getByRole("button", { name: "More solution actions" }).click();
	await page.getByRole("menuitem", { name: "Uninstall" }).click();
	await expectPersistedStatus(api, solutionId, "inactive");

	await page.reload();
	await expect(page.getByRole("heading", { name: solutionName })).toBeVisible(
		{
			timeout: 15000,
		},
	);
	await expect(page.getByText("Inactive", { exact: true })).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Reactivate" }),
	).toBeVisible();
	await page.getByRole("button", { name: "More solution actions" }).click();
	await expect(
		page.getByRole("menuitem", { name: "Uninstall" }),
	).not.toBeVisible();
	await expect(
		page.getByRole("menuitem", { name: "Delete permanently" }),
	).toBeVisible();
	await page.keyboard.press("Escape");

	await page.goto("/solutions");
	await expect(
		page.getByRole("heading", { name: "Solutions", exact: true }),
	).toBeVisible({ timeout: 10000 });
	await expect(
		page.getByRole("link", { name: new RegExp(slug, "i") }),
	).not.toBeVisible();
	await page.getByRole("switch", { name: "Show Inactive" }).click();
	await expect(
		page.getByRole("link", { name: new RegExp(slug, "i") }),
	).toBeVisible();
});
test.describe("Inactive solution", () => {
	test.use({ solutionStatus: "inactive" });
	test("reactivates an inactive solution from an uploaded package", async ({
		page,
		api,
		solution: { solutionId, slug, solutionName },
	}) => {
		await page.goto(`/solutions/${solutionId}`);
		await page.getByRole("button", { name: "Reactivate" }).click();
		await expect(
			page.getByRole("dialog", { name: "Reactivate Solution" }),
		).toBeVisible();
		await page.locator('input[type="file"]').setInputFiles({
			name: `${slug}.zip`,
			mimeType: "application/zip",
			buffer: minimalSolutionZip(slug),
		});
		await expect(page.getByText(`Upgrade ${solutionName}`)).toBeVisible({
			timeout: 15000,
		});
		const reactivateResponse = page.waitForResponse(
			(response) =>
				new URL(response.url()).pathname === "/api/solutions/install" &&
				new URL(response.url()).searchParams.get("reactivate") ===
					"true" &&
				response.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Upgrade" }).click();
		const response = await reactivateResponse;
		if (response.status() !== 202) {
			throw new Error(`reactivate solution: ${await response.text()}`);
		}
		const { deploy_job_id: deployJobId } = (await response.json()) as {
			deploy_job_id: string;
		};
		await expectDeployJobSucceeded(api, deployJobId);
		await expectPersistedStatus(api, solutionId, "active");

		await page.reload();
		await expect(
			page.getByRole("heading", { name: solutionName }),
		).toBeVisible({
			timeout: 15000,
		});
		await expect(
			page.getByRole("button", { name: "Update", exact: true }),
		).toBeVisible();
		await expect(
			page.getByText("Inactive", { exact: true }),
		).not.toBeVisible();
	});
});
test("permanently deletes a solution with slug confirmation", async ({
	page,
	api,
	solution: { solutionId, slug, solutionName },
}) => {
	await page.goto(`/solutions/${solutionId}`);

	await page.getByRole("button", { name: "More solution actions" }).click();
	await page.getByRole("menuitem", { name: "Delete permanently" }).click();
	await expect(
		page.getByRole("dialog", {
			name: `Permanently delete ${solutionName}?`,
		}),
	).toBeVisible();
	await expect(
		page.getByRole("button", { name: "Delete permanently" }),
	).toBeDisabled();
	await page
		.getByRole("textbox", {
			name: "Type the Solution slug to confirm",
		})
		.fill(slug);
	await expect(
		page.getByRole("button", { name: "Delete permanently" }),
	).toBeEnabled();
	await page.getByRole("button", { name: "Delete permanently" }).click();
	await expect(page).toHaveURL(/\/solutions$/);

	await expect
		.poll(async () =>
			(await api.get(`/api/solutions/${solutionId}`)).status(),
		)
		.toBe(404);
});
