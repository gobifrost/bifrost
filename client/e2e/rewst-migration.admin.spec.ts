import { Buffer } from "node:buffer";
import { readFile, readdir } from "node:fs/promises";
import { join, relative, sep } from "node:path";
import { expect, test, type AuthedApi } from "./fixtures/api-fixture";

const EXAMPLE_ROOT = join(process.cwd(), "examples", "rewst-migration");
const SOLUTION_SLUG = "rewst-migration";
const EXCLUDED_DIRECTORIES = new Set(["node_modules", "dist", "__pycache__"]);

type ZipEntry = { path: string; content: Buffer };

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

function buildStoreOnlyZip(entries: ZipEntry[]): Buffer {
	const localParts: Buffer[] = [];
	const centralParts: Buffer[] = [];
	let offset = 0;

	for (const entry of entries) {
		const name = Buffer.from(entry.path);
		const checksum = crc32(entry.content);
		const local = Buffer.alloc(30);
		local.writeUInt32LE(0x04034b50, 0);
		local.writeUInt16LE(20, 4);
		local.writeUInt16LE(0, 6);
		local.writeUInt16LE(0, 8);
		local.writeUInt32LE(checksum, 14);
		local.writeUInt32LE(entry.content.length, 18);
		local.writeUInt32LE(entry.content.length, 22);
		local.writeUInt16LE(name.length, 26);
		local.writeUInt16LE(0, 28);
		localParts.push(local, name, entry.content);

		const central = Buffer.alloc(46);
		central.writeUInt32LE(0x02014b50, 0);
		central.writeUInt16LE(20, 4);
		central.writeUInt16LE(20, 6);
		central.writeUInt16LE(0, 8);
		central.writeUInt16LE(0, 10);
		central.writeUInt32LE(checksum, 16);
		central.writeUInt32LE(entry.content.length, 20);
		central.writeUInt32LE(entry.content.length, 24);
		central.writeUInt16LE(name.length, 28);
		central.writeUInt16LE(0, 30);
		central.writeUInt16LE(0, 32);
		central.writeUInt32LE(offset, 42);
		centralParts.push(central, name);
		offset += local.length + name.length + entry.content.length;
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

async function sourceEntries(directory = EXAMPLE_ROOT): Promise<ZipEntry[]> {
	const entries: ZipEntry[] = [];
	for (const entry of await readdir(directory, { withFileTypes: true })) {
		if (entry.isDirectory()) {
			if (!EXCLUDED_DIRECTORIES.has(entry.name)) {
				entries.push(
					...(await sourceEntries(join(directory, entry.name))),
				);
			}
			continue;
		}
		if (
			!entry.isFile() ||
			entry.name === ".env" ||
			entry.name.startsWith(".env.")
		)
			continue;

		const absolutePath = join(directory, entry.name);
		entries.push({
			path: relative(EXAMPLE_ROOT, absolutePath).split(sep).join("/"),
			content: await readFile(absolutePath),
		});
	}
	return entries.sort((left, right) => left.path.localeCompare(right.path));
}

async function exampleSolutionZip(): Promise<Buffer> {
	const entries = await sourceEntries();
	const paths = entries.map((entry) => entry.path);
	expect(paths).toEqual(
		expect.arrayContaining([
			"bifrost.solution.yaml",
			".bifrost/apps.yaml",
			".bifrost/files.yaml",
			".bifrost/workflows.yaml",
			"apps/rewst-migration/src/App.tsx",
			"functions/migration.py",
			"modules/rewst_migration/core.py",
		]),
	);
	return buildStoreOnlyZip(entries);
}

async function waitForDeploy(api: AuthedApi, deployJobId: string) {
	await expect
		.poll(
			async () => {
				const response = await api.get(
					`/api/solutions/deploy-jobs/${deployJobId}`,
				);
				expect(
					response.ok(),
					`poll deploy job: ${await response.text()}`,
				).toBe(true);
				const job = (await response.json()) as {
					status: string;
					error?: string | null;
				};
				if (job.status === "failed") {
					throw new Error(
						job.error || "Rewst Migration deploy failed",
					);
				}
				return job.status;
			},
			{ timeout: 90_000 },
		)
		.toBe("succeeded");
}

test.describe("Rewst Migration Solution (admin)", () => {
	test.use({ viewport: { width: 1440, height: 900 } });

	test("installs the real sample workflow and persists its saved plan", async ({
		page,
		api,
	}) => {
		// This test intentionally owns one deploy/build job. It is the representative
		// cross-process contract for the example's source app, SDK workflows, and
		// revisioned managed-file persistence. Later browser actions use SDK workflow
		// executions but enqueue no additional deploy, install, publish, or build job.
		test.setTimeout(120_000);
		const pageErrors: string[] = [];
		page.on("pageerror", (error) => pageErrors.push(error.message));
		await page.addInitScript(() => localStorage.setItem("theme", "dark"));
		let solutionId: string | null = null;
		let primaryError: unknown;
		try {
			const created = await api.post("/api/solutions", {
				data: {
					slug: SOLUTION_SLUG,
					name: "Rewst Migration",
					organization_id: null,
					allow_outbound_access: false,
					allow_inbound_access: true,
				},
			});
			expect(
				created.ok(),
				`create Rewst Migration: ${await created.text()}`,
			).toBe(true);
			solutionId = ((await created.json()) as { id: string }).id;

			const deployed = await api.post(
				`/api/solutions/${solutionId}/deploy`,
				{
					multipart: {
						file: {
							name: "rewst-migration.zip",
							mimeType: "application/zip",
							buffer: await exampleSolutionZip(),
						},
					},
				},
			);
			expect(
				deployed.status(),
				`deploy Rewst Migration: ${await deployed.text()}`,
			).toBe(202);
			const { deploy_job_id: deployJobId } = (await deployed.json()) as {
				deploy_job_id: string;
			};
			await waitForDeploy(api, deployJobId);

			await page.goto(`/apps/${SOLUTION_SLUG}`);
			await expect(
				page.getByRole("heading", {
					name: "Prepare a Rewst migration",
				}),
			).toBeVisible();
			await expect(
				page.getByText("Setup required", { exact: true }),
			).toBeVisible();

			await page
				.getByRole("button", { name: "Try a Sample Migration" })
				.click();
			await expect(
				page.getByRole("heading", { name: "Sample Rewst migration" }),
			).toBeVisible();

			const exportEnvironment = page.getByRole("button", {
				name: "Export environment",
			});
			await exportEnvironment.click();
			await expect(
				page.getByRole("button", { name: "Start a new migration" }),
			).toBeVisible();

			await page.getByRole("link", { name: "Plan", exact: true }).click();
			await expect(
				page.getByRole("heading", { name: "Implementation plan" }),
			).toBeVisible();
			const savedPlan = `# Browser acceptance ${crypto.randomUUID()}`;
			await page.getByLabel("Migration plan").fill(savedPlan);
			await page.getByRole("button", { name: "Save plan" }).click();
			await expect(
				page.getByText("Plan saved.", { exact: true }),
			).toBeVisible();

			const firstItem = page.getByRole("article").filter({
				has: page.getByRole("heading", {
					name: "Sample onboarding",
					exact: true,
				}),
			});
			await firstItem
				.getByRole("textbox")
				.fill("Synthetic sandbox replay passed");
			await firstItem.getByRole("combobox").selectOption("tested");
			await expect(
				firstItem.getByText("Current state: tested", { exact: true }),
			).toBeVisible();
			await page.screenshot({
				path: test.info().outputPath("rewst-plan-desktop.png"),
			});

			await page.reload();
			await expect(page.getByLabel("Migration plan")).toHaveValue(
				savedPlan,
			);

			await expect(firstItem.getByRole("textbox")).toHaveValue(
				"Synthetic sandbox replay passed",
			);
			await expect(
				firstItem.getByText("Current state: tested", { exact: true }),
			).toBeVisible();

			const firstMigrationId = await page
				.getByLabel("Migration", { exact: true })
				.inputValue();
			const unsavedDraft = `${savedPlan}\nUnsaved draft belongs only to migration A.`;
			await page.getByLabel("Migration plan").fill(unsavedDraft);
			await page
				.getByRole("link", { name: "Setup", exact: true })
				.click();
			await page
				.getByRole("button", { name: "Try a Sample Migration" })
				.click();
			await page
				.getByRole("heading", { name: "Sample Rewst migration" })
				.waitFor();
			await page.getByRole("link", { name: "Plan", exact: true }).click();
			await expect(page.getByLabel("Migration plan")).not.toHaveValue(
				unsavedDraft,
			);
			await expect(page.getByLabel("Migration plan")).toHaveValue(
				/Intake and authority/,
			);
			await page
				.getByLabel("Migration", { exact: true })
				.selectOption(firstMigrationId);
			await expect(page.getByLabel("Migration plan")).toHaveValue(
				unsavedDraft,
			);

			await page
				.getByRole("link", { name: "Agent Handoff", exact: true })
				.click();
			await expect(
				page.getByRole("heading", { name: "Agent handoff" }),
			).toBeVisible();
			await expect(
				page.getByText("Synthetic sample inventory", { exact: true }),
			).toBeVisible();
			const planArtifact = page
				.getByRole("listitem")
				.filter({
					has: page.getByText("Migration plan revision 2", {
						exact: true,
					}),
				});
			await planArtifact
				.getByRole("button", { name: "Read artifact" })
				.click();
			await expect(page.getByLabel("Artifact content")).toContainText(
				savedPlan,
			);
			const sourceArtifact = page
				.getByRole("listitem")
				.filter({
					has: page.getByText("Synthetic sample inventory", {
						exact: true,
					}),
				});
			await sourceArtifact
				.getByRole("button", { name: "Read artifact" })
				.click();
			await expect(page.getByLabel("Artifact content")).toContainText(
				"sample-workflow-onboarding",
			);
			await page.setViewportSize({ width: 390, height: 844 });
			await expect(
				page.getByRole("heading", { name: "Agent handoff" }),
			).toBeVisible();
			const commands = page.getByLabel("Suggested CLI commands");
			await commands.scrollIntoViewIfNeeded();
			await expect(commands).toContainText("solution_id");
			expect(
				await commands.evaluate(
					(element) => element.scrollWidth <= element.clientWidth + 1,
				),
				"CLI payloads wrap within the phone viewport",
			).toBe(true);
			await page.screenshot({
				path: test.info().outputPath("rewst-handoff-mobile.png"),
			});
			await page.getByRole("button", { name: "Open menu", exact: true }).click();
			const menu = page.getByRole("dialog", { name: "Menu", exact: true });
			await expect(menu).toBeVisible();
			await menu.getByRole("link", { name: "Plan", exact: true }).click();
			await expect(menu).toBeHidden();
			await expect(page.getByLabel("Migration plan")).toHaveValue(unsavedDraft);
			await page.getByRole("button", { name: "Open menu", exact: true }).click();
			await menu.getByRole("button", { name: "Light mode", exact: true }).click();
			await expect(page.locator("html")).not.toHaveClass(/dark/);
			await page.getByRole("button", { name: "Close menu", exact: true }).click();
			await page.screenshot({
				path: test.info().outputPath("rewst-plan-mobile-light.png"),
			});
			expect(pageErrors).toEqual([]);
		} catch (error) {
			primaryError = error;
		} finally {
			if (solutionId) {
				try {
					const removed = await api.delete(
						`/api/solutions/${solutionId}`,
						{ params: { confirm: SOLUTION_SLUG } },
					);
					expect(
						[200, 204, 404],
						`remove Rewst Migration: ${await removed.text()}`,
					).toContain(removed.status());
				} catch (cleanupError) {
					// Keep a failed deployment's original error visible when its
					// still-active write lock also prevents teardown.
					if (primaryError) {
						await test.info().attach("cleanup-failure", {
							body: String(cleanupError),
							contentType: "text/plain",
						});
					} else {
						primaryError = cleanupError;
					}
				}
			}
		}
		if (primaryError) throw primaryError;
	});
});
